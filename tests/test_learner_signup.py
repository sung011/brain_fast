"""학습자 가입 POST /signup. 학교·전공·생년월일을 저장하고 비밀번호는 응답에 넣지 않는다."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from config import settings
from db import get_db
from routers import commonness, user
from schemas.userSchemas import LearnerSignup

try:
    from temp_pg import HAVE_DB_TOOLS
    import psycopg  # noqa: F401
    if not HAVE_DB_TOOLS:
        raise ImportError("temp pg backend missing")
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False

PASSWORD = "synthetic-pass-1234"
# 이메일 인증 켜짐 → 질문 1개, 꺼짐 → 기존 3개. .env 설정과 스키마 검증을 맞춘다.
ANSWERS_LEGACY = [
    {"question_id": "Q01", "answer": "합성 초등학교"},
    {"question_id": "Q02", "answer": "합성별명"},
    {"question_id": "Q03", "answer": "합성동"},
]
ANSWERS_CHOICE = [{"question_id": "Q01", "answer": "합성 초등학교"}]
ANSWERS = ANSWERS_CHOICE if settings.signup_email_verification_enabled else ANSWERS_LEGACY


class LearnerSignupSchemaTests(unittest.TestCase):
    def test_accepts_school_major_and_compact_birth(self) -> None:
        body = LearnerSignup(
            user_id="learner@example.test",
            user_pw="pass-1234",
            user_name="학습",
            user_birth="20000131",
            user_un="합성대학교",
            user_sp="방사선학과",
            recovery_answers=ANSWERS,
        )
        self.assertEqual(body.user_birth, "2000-01-31")
        short = LearnerSignup(
            user_id="short-birth@example.test",
            user_pw="pass-1234",
            user_name="학습",
            user_birth="880324",
            user_un="합성대학교",
            user_sp="방사선학과",
            recovery_answers=ANSWERS,
        )
        self.assertEqual(short.user_birth, "1988-03-24")
        self.assertEqual(body.user_un, "합성대학교")
        self.assertEqual(body.user_sp, "방사선학과")

    def test_rejects_under_14_and_future_birth(self) -> None:
        today = date.today().isoformat()
        with self.assertRaises(ValidationError):
            LearnerSignup(
                user_id="young@example.test",
                user_pw="pass-1234",
                user_name="학습",
                user_birth=today,
                user_un="학교",
                user_sp="전공",
                recovery_answers=ANSWERS,
            )
        with self.assertRaises(ValidationError):
            LearnerSignup(
                user_id="future@example.test",
                user_pw="pass-1234",
                user_name="학습",
                user_birth="2999-01-01",
                user_un="학교",
                user_sp="전공",
                recovery_answers=ANSWERS,
            )

    def test_bad_birth_is_422_without_a_server_error(self) -> None:
        from fastapi import FastAPI
        from fastapi.exceptions import RequestValidationError

        from exception_handlers import validation_exception_handler

        app = FastAPI()
        app.add_exception_handler(RequestValidationError, validation_exception_handler)

        @app.post("/signup")
        def signup(body: LearnerSignup):
            return {"ok": True}

        response = TestClient(app, raise_server_exceptions=False).post(
            "/signup",
            json={
                "user_id": "bad-birth@example.test",
                "user_pw": "pass-1234",
                "user_name": "학습",
                "user_birth": "881332",
                "user_un": "학교",
                "user_sp": "전공",
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("올바른 생년월일", response.text)
        self.assertNotIn("is not JSON serializable", response.text)


@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg 가 없어 임시 DB 시험을 건너뜀")
class LearnerSignupApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        from config import settings

        # 이 시험은 이메일 인증 끄고 질문 답만으로 가입하는 경로를 본다.
        cls._prev_email = settings.signup_email_verification_enabled
        settings.signup_email_verification_enabled = False

        cls.h = Harness()
        from models.userRecoveryAnswerModel import UserRecoveryAnswerModel
        UserRecoveryAnswerModel.__table__.create(cls.h.engine)   # 하네스는 회원·문제·제출 테이블만 만든다
        app = FastAPI()
        app.include_router(user.router)
        app.include_router(commonness.router)

        def get_session():
            with cls.h.Session() as db:
                yield db

        app.dependency_overrides[get_db] = get_session
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        from config import settings

        settings.signup_email_verification_enabled = getattr(cls, "_prev_email", False)
        cls.h.close()

    def test_signup_saves_profile_and_login_has_no_password_fields(self) -> None:
        # 이메일 인증을 끈 경로이므로 복구 질문 3개를 넣는다.
        answers = ANSWERS_LEGACY
        created = self.client.post(
            "/signup",
            json={
                "user_id": "signup-new@example.test",
                "user_pw": PASSWORD,
                "user_name": "가입",
                "user_birth": "2000-05-05",
                "user_un": "합성대학교",
                "user_sp": "의학과",
                "recovery_answers": answers,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["user_name"], "가입")
        self.assertNotIn(PASSWORD, created.text)
        self.assertNotIn("user_pw", body)

        again = self.client.post(
            "/signup",
            json={
                "user_id": "signup-new@example.test",
                "user_pw": PASSWORD,
                "user_name": "가입",
                "user_birth": "2000-05-05",
                "user_un": "합성대학교",
                "user_sp": "의학과",
                "recovery_answers": answers,
            },
        )
        self.assertEqual(again.status_code, 409)

        logged = self.client.post(
            "/login",
            json={"user_id": "signup-new@example.test", "user_pw": "wrong-pass"},
        )
        self.assertEqual(logged.status_code, 401)
        self.assertEqual(logged.json()["detail"]["message"], "아이디 또는 비밀번호가 올바르지 않습니다.")
        self.assertNotIn(PASSWORD, logged.text)
        self.assertNotIn("plain_pw", logged.text)
        self.assertNotIn("db_user_pw", logged.text)
