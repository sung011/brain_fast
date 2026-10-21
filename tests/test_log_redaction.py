"""
LOGIN-019-01 수정(사용자 승인 2026-10-13: A·C) 시험 — 서버 로그와 콘솔에 비밀번호가 남지 않는지 확인한다.

A: 공용 오류 처리기(exception_handlers.py)가 로그에 적기 전에 비밀번호 같은 값을 가린다. **응답은 그대로**(사용자 지시: 로그인 응답의 개발용 필드 유지).
C: 관리자 로그인(routers/admin.py)의 `print(body)`(입력한 비밀번호가 콘솔로 나감)를 없앤다.

합성 계정·합성 비밀번호만 쓴다(실제 계정·재사용 비밀번호 금지). 로그는 파일이 아니라 **메모리 핸들러**로 잡고, 실제 로그 폴더에는 아무것도 쓰지 않는다.
실행: python -m pytest tests/test_log_redaction.py   (DB 시험은 pgserver, psycopg, pillow 필요. 없으면 건너뜀)
"""
import contextlib
import io
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

import exception_handlers as handlers
from db import get_db
from logging_setup import ERROR_LOGGER

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    import PIL  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False

SECRET = "Synthetic-Pw-9f3k2x7q"           # 합성 비밀번호(실제 값 아님)
SECRET_NUM = 918273645


class MemoryLog(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())

    @property
    def text(self):
        return "\n".join(self.messages)


def capture_error_log(testcase):
    handler = MemoryLog()
    logger = logging.getLogger(ERROR_LOGGER)
    previous_level, previous_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False        # 시험 중에는 실제 파일 로그에 쓰지 않는다
    def restore():
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
    testcase.addCleanup(restore)
    return handler


def make_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="redaction-test-secret")
    app.add_exception_handler(RequestValidationError, handlers.validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, handlers.http_exception_handler)
    app.add_exception_handler(Exception, handlers.unhandled_exception_handler)
    return app


# ── 가림 함수 단위 시험 ────────────────────────────────────────────────────────────────
class RedactFunctionTests(unittest.TestCase):
    def setUp(self):
        self.redact = getattr(handlers, "redact_sensitive", None)
        self.assertIsNotNone(self.redact, "exception_handlers.redact_sensitive 가 있어야 한다")

    def test_dict_values_of_sensitive_keys_are_masked_recursively(self):
        detail = {"message": "아이디 또는 비밀번호가 올바르지 않습니다.", "plain_pw": SECRET, "hashed_pw": "$2b$12$abc", "db_user_pw": "$2b$12$def",
                  "pw_match": False, "nested": {"user_pw": SECRET, "ok": 1}, "list": [{"password": SECRET}, "text"]}
        out = self.redact(detail)
        self.assertNotIn(SECRET, repr(out))
        self.assertNotIn("$2b$", repr(out))
        self.assertEqual(out["message"], detail["message"])                       # 비밀번호와 무관한 값은 그대로
        self.assertEqual(out["nested"]["ok"], 1)
        self.assertEqual(out["list"][1], "text")
        self.assertEqual(out["plain_pw"], "[REDACTED]")

    def test_key_variants_are_masked(self):
        for key in ("PLAIN_PW", "plainPw", "user-pw", "userPw", "Password", "PASSWD", "token", "problem_token", "Authorization", "api_key"):
            with self.subTest(key=key):
                self.assertEqual(self.redact({key: SECRET})[key], "[REDACTED]")

    def test_text_forms_are_masked(self):
        for text in (f"user_id='a@b.c' user_pw='{SECRET}'", f'{{"plain_pw": "{SECRET}", "x": 1}}', f"{{'plain_pw': '{SECRET}'}}", f"password={SECRET} next",
                     f"body : user_id='a' user_pw={SECRET}"):
            with self.subTest(text=text):
                out = self.redact(text)
                self.assertNotIn(SECRET, out)
                self.assertIn("[REDACTED]", out)

    def test_plain_text_without_secrets_is_unchanged(self):
        for text in ("박스는 이미지 안에 있어야 합니다.", "로그인이 필요합니다.", "slice_index 가 슬라이드 범위를 벗어났습니다."):
            self.assertEqual(self.redact(text), text)
        self.assertEqual(self.redact({"detail": "ok", "n": 3, "flag": None}), {"detail": "ok", "n": 3, "flag": None})

    def test_never_raises_and_never_returns_the_original_on_failure(self):
        class Bad:
            def __repr__(self):
                raise RuntimeError("boom " + SECRET)
        out = self.redact({"x": Bad()})
        self.assertNotIn(SECRET, repr(out))
        self.assertIsNotNone(self.redact(Bad()))

    def test_idempotent(self):
        once = self.redact({"plain_pw": SECRET, "a": "b"})
        self.assertEqual(self.redact(once), once)
        text_once = self.redact(f"user_id='a' user_pw='{SECRET}' password={SECRET}")
        self.assertEqual(self.redact(text_once), text_once)                      # 글자 형태도 여러 번 가려도 같다
        self.assertNotIn("]]", text_once)

    def test_text_scrub_failure_never_returns_the_original(self):
        out = handlers.scrub_text(SECRET_NUM)                                    # 글자가 아닌 값을 넣어 일부러 실패시킨다
        self.assertNotIn(str(SECRET_NUM), str(out))
        self.assertTrue(str(out).startswith("[REDACTED"))


# ── 공용 처리기: 실제 처리기와 합성 라우트 ────────────────────────────────────────────────────
class HandlerLogTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()

        # 비밀 값은 raise 줄(로그의 "코드" 줄에 소스가 찍힌다)이 아니라 변수에서 가져온다. 운영 코드(**debug)와 같은 모양이다.
        detail_with_secrets = {"message": "아이디 또는 비밀번호가 올바르지 않습니다.", "plain_pw": SECRET, "hashed_pw": "$2b$12$zzz"}

        @self.app.post("/boom401")
        async def boom401():
            raise HTTPException(status_code=401, detail=detail_with_secrets)

        @self.app.post("/boom400")
        async def boom400():
            raise HTTPException(status_code=400, detail="박스는 이미지 안에 있어야 합니다.")

        @self.app.post("/boom400dict")
        async def boom400dict():
            raise HTTPException(status_code=400, detail={"message": "입력 오류", "field": "x"})

        @self.app.post("/boom401text")
        async def boom401text():
            raise HTTPException(status_code=401, detail=f"body : user_id='a' user_pw='{SECRET}'")

        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.log = capture_error_log(self)

    def test_http_error_log_has_no_password_but_keeps_the_context(self):
        res = self.client.post("/boom401")
        self.assertEqual(res.status_code, 401)
        self.assertNotIn(SECRET, self.log.text)
        self.assertNotIn("$2b$12$zzz", self.log.text)
        self.assertIn("[REDACTED]", self.log.text)
        self.assertIn("401", self.log.text)                                     # 상태 코드
        self.assertIn("/boom401", self.log.text)                                # 요청 경로
        self.assertIn("아이디 또는 비밀번호가 올바르지 않습니다.", self.log.text)       # 비밀번호와 무관한 내용은 남는다

    def test_response_body_is_unchanged_by_the_log_fix(self):
        """A 안: 응답은 그대로(사용자 지시). 로그만 가린다."""
        body = self.client.post("/boom401").json()
        self.assertEqual(body["detail"]["plain_pw"], SECRET)
        self.assertEqual(body["detail"]["hashed_pw"], "$2b$12$zzz")
        self.assertEqual(body["error_type"], "http_error")
        self.assertFalse(body["ok"])

    def test_text_detail_is_scrubbed_in_the_log(self):
        self.client.post("/boom401text")
        self.assertNotIn(SECRET, self.log.text)

    def test_ordinary_errors_are_logged_intact(self):
        self.client.post("/boom400")
        self.client.post("/boom400dict")
        self.assertIn("박스는 이미지 안에 있어야 합니다.", self.log.text)
        self.assertIn("입력 오류", self.log.text)
        self.assertNotIn("[REDACTED]", self.log.text)                           # 과하게 가리지 않는다

    def test_validation_error_log_does_not_echo_password_inputs(self):
        from pydantic import BaseModel

        class Body(BaseModel):
            user_id: str
            user_pw: str

        @self.app.post("/validate")
        async def validate(body: Body):
            return {"ok": True}

        for payload in ({"user_pw": SECRET}, {"user_id": "a", "user_pw": SECRET_NUM}, {"user_id": "a", "user_pw": [SECRET]}):
            with self.subTest(payload=payload):
                before = len(self.log.messages)
                res = self.client.post("/validate", json=payload)
                self.assertEqual(res.status_code, 422)
                logged = "\n".join(self.log.messages[before:])
                self.assertNotIn(SECRET, logged)
                self.assertNotIn(str(SECRET_NUM), logged)
                self.assertIn("위치=", logged)                                  # 어느 필드가 왜 틀렸는지는 남는다

    def test_validation_response_is_unchanged(self):
        from pydantic import BaseModel

        class Body(BaseModel):
            user_id: str

        @self.app.post("/validate2")
        async def validate2(body: Body):
            return {"ok": True}

        res = self.client.post("/validate2", json={"user_pw": SECRET})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["error_type"], "validation_error")

    def test_unhandled_exception_log_has_no_password_text(self):
        @self.app.post("/crash")
        async def crash():
            raise RuntimeError(f"failed with user_pw='{SECRET}'")

        res = self.client.post("/crash")
        self.assertEqual(res.status_code, 500)
        self.assertNotIn(SECRET, self.log.text)


# ── 실제 로그인 두 곳(학습자·관리자)과 임시 DB ──────────────────────────────────────────────
@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg/pillow 가 없어 임시 DB 시험을 건너뜀")
class RealLoginLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        from routers import admin, commonness
        cls.h = Harness()
        app = make_app()
        app.include_router(commonness.router)
        app.include_router(admin.router)

        def get_session():
            with cls.h.Session() as db:
                yield db

        app.dependency_overrides[get_db] = get_session
        cls.app = app

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    def setUp(self):
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.log = capture_error_log(self)

    def test_learner_login_failure_does_not_leave_the_password_in_the_log(self):
        res = self.client.post("/login", json={"user_id": self.h.login_id, "user_pw": SECRET})
        self.assertEqual(res.status_code, 401)
        self.assertNotIn(SECRET, self.log.text)
        self.assertIn("/login", self.log.text)
        self.assertIn("401", self.log.text)
        # 응답은 사용자 지시대로 그대로(개발용 필드 유지). 이 줄이 실패하면 응답을 바꾼 것이다.
        self.assertEqual(res.json()["detail"]["plain_pw"], SECRET)

    def test_learner_login_unknown_user_and_validation_failure(self):
        res = self.client.post("/login", json={"user_id": "nobody@example.test", "user_pw": SECRET})
        self.assertEqual(res.status_code, 401)
        res = self.client.post("/login", json={"user_pw": SECRET})
        self.assertEqual(res.status_code, 422)
        self.assertNotIn(SECRET, self.log.text)

    def test_learner_login_success_still_works_and_logs_nothing_sensitive(self):
        res = self.client.post("/login", json={"user_id": self.h.login_id, "user_pw": self.h.login_pw})
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(self.h.login_pw, self.log.text)

    def test_admin_login_does_not_print_the_password_to_the_console(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            res = self.client.post("/admin/login", json={"user_id": self.h.login_id, "user_pw": SECRET})
        self.assertEqual(res.status_code, 401)
        self.assertNotIn(SECRET, out.getvalue())
        self.assertNotIn(SECRET, err.getvalue())
        self.assertNotIn(SECRET, self.log.text)

    def test_admin_login_still_works(self):
        res = self.client.post("/admin/login", json={"user_id": self.h.login_id, "user_pw": self.h.login_pw})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["user_id"], self.h.login_id)


# ── Authorization / Bearer 같은 접속 증명 값(FINDING-LOG-021-02) ──────────────────────────────────────────────
# 키 뒤의 값이 `Bearer <토큰>` 처럼 공백이 있는 형태여도 토큰까지 로그에서 가려야 한다. 응답은 그대로 둔다.
BEARER = "synthetic.bearer-Tok3n_9f3k2x7q"    # 합성 토큰(실제 값 아님, JWT 처럼 점·하이픈·밑줄 포함)
BEARER_B64 = "c3ludGhldGljOnVzZXI6cHc9"         # 합성 Basic 값


class CredentialLogTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        carried = {"Authorization": f"Bearer {BEARER}", "note": "ok"}

        @self.app.post("/e/text-colon")
        async def e_text_colon():
            raise HTTPException(status_code=401, detail=f"Authorization: Bearer {BEARER}")

        @self.app.post("/e/text-equals")
        async def e_text_equals():
            raise HTTPException(status_code=401, detail=f"authorization=Bearer {BEARER} next")

        @self.app.post("/e/text-quoted")
        async def e_text_quoted():
            raise HTTPException(status_code=401, detail=f"headers={{'Authorization': 'Bearer {BEARER}', 'x': 1}}")

        @self.app.post("/e/text-json")
        async def e_text_json():
            raise HTTPException(status_code=401, detail=f'{{"authorization": "Bearer {BEARER}"}}')

        @self.app.post("/e/bare")
        async def e_bare():
            raise HTTPException(status_code=401, detail=f"rejected credential bearer {BEARER} for this request")

        @self.app.post("/e/basic")
        async def e_basic():
            raise HTTPException(status_code=401, detail=f"Authorization: Basic {BEARER_B64}")

        @self.app.post("/e/dict")
        async def e_dict():
            raise HTTPException(status_code=401, detail=carried)

        @self.app.post("/e/crash")
        async def e_crash():
            raise RuntimeError(f"upstream refused Authorization: Bearer {BEARER}")

        @self.app.get("/e/query")
        async def e_query():
            raise HTTPException(status_code=400, detail="bad request")

        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.log = capture_error_log(self)

    def assert_clean(self, text):
        self.assertNotIn(BEARER, text)
        self.assertNotIn(BEARER_B64, text)
        self.assertNotIn("Tok3n", text)

    def test_http_error_with_a_bearer_credential_in_the_detail(self):
        for path in ("/e/text-colon", "/e/text-equals", "/e/text-quoted", "/e/text-json", "/e/bare", "/e/basic", "/e/dict"):
            with self.subTest(path=path):
                before = len(self.log.messages)
                res = self.client.post(path)
                self.assertEqual(res.status_code, 401)
                logged = "\n".join(self.log.messages[before:])
                self.assert_clean(logged)
                self.assertIn("[REDACTED]", logged)
                self.assertIn(path, logged)                                     # 요청 경로·상태 코드는 남는다
                self.assertIn("401", logged)

    def test_the_response_is_unchanged(self):
        self.assertEqual(self.client.post("/e/text-colon").json()["detail"], f"Authorization: Bearer {BEARER}")
        self.assertEqual(self.client.post("/e/dict").json()["detail"], {"Authorization": f"Bearer {BEARER}", "note": "ok"})

    def test_server_error_log_has_no_bearer_credential(self):
        res = self.client.post("/e/crash")
        self.assertEqual(res.status_code, 500)
        self.assert_clean(self.log.text)
        self.assertIn("/e/crash", self.log.text)

    def test_request_headers_sent_with_the_call_never_reach_the_log(self):
        res = self.client.post("/e/text-colon", headers={"Authorization": f"Bearer {BEARER}"})
        self.assertEqual(res.status_code, 401)
        self.assert_clean(self.log.text)

    def test_query_string_credentials_are_masked(self):
        res = self.client.get("/e/query", params={"token": BEARER, "access_token": BEARER, "password": SECRET, "page": "2"})
        self.assertEqual(res.status_code, 400)
        self.assert_clean(self.log.text)
        self.assertNotIn(SECRET, self.log.text)
        self.assertIn("page=2", self.log.text)                                  # 비밀이 아닌 값은 남는다

    def test_validation_error_input_with_a_bearer_credential(self):
        from pydantic import BaseModel

        class Body(BaseModel):
            count: int

        @self.app.post("/e/validate")
        async def e_validate(body: Body):
            return {"ok": True}

        res = self.client.post("/e/validate", json={"count": f"Authorization: Bearer {BEARER}"})
        self.assertEqual(res.status_code, 422)
        self.assert_clean(self.log.text)
        self.assertIn("위치=", self.log.text)

    def test_function_level_forms_and_no_over_masking(self):
        for text in (f"Authorization: Bearer {BEARER}", f"authorization=Bearer {BEARER}", f"Bearer {BEARER}", f"BEARER {BEARER}",
                     f"Authorization: Basic {BEARER_B64}", f"headers={{'authorization': 'Bearer {BEARER}'}}"):
            with self.subTest(text=text):
                self.assert_clean(handlers.redact_sensitive(text))
                self.assertEqual(handlers.redact_sensitive(handlers.redact_sensitive(text)), handlers.redact_sensitive(text))   # 여러 번 가려도 같다
        for text in ("Bearer 토큰이 필요합니다.", "carrier bearer of news", "the bearer", "page=2&sort=name"):
            with self.subTest(plain=text):
                self.assertEqual(handlers.redact_sensitive(text), text)         # 비밀이 아닌 글은 그대로


class SourceChecks(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_admin_login_has_no_debug_print_of_the_body(self):
        source = (self.ROOT / "routers" / "admin.py").read_text(encoding="utf-8")
        login = source[source.index("def login_post"):source.index("@router.get(\"/logout\")")]
        self.assertNotIn("print(", login)
        self.assertNotIn("body :", login)

    def test_only_the_approved_shared_files_changed_for_this_fix(self):
        """승인 범위(A: exception_handlers.py, C: routers/admin.py) 밖의 공용 로그인 코드는 이 수정으로 바뀌지 않았다."""
        commonness = (self.ROOT / "routers" / "commonness.py").read_text(encoding="utf-8")
        self.assertIn('"plain_pw": body.user_pw,', commonness)                  # 응답의 개발용 필드는 그대로(사용자 지시)
        self.assertIn('request.session["learner"]', commonness)


if __name__ == "__main__":
    unittest.main()
