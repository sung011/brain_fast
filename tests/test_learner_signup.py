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

from db import get_db
from routers import commonness, user
from schemas.userSchemas import LearnerSignup

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False

PASSWORD = "synthetic-pass-1234"


class LearnerSignupSchemaTests(unittest.TestCase):
    def test_accepts_school_major_and_compact_birth(self) -> None:
        body = LearnerSignup(
            user_id="learner@example.test",
            user_pw="pass-1234",
            user_name="학습",
            user_birth="20000131",
            user_un="합성대학교",
            user_sp="방사선학과",
        )
        self.assertEqual(body.user_birth, "2000-01-31")
        short = LearnerSignup(
            user_id="short-birth@example.test",
            user_pw="pass-1234",
            user_name="학습",
            user_birth="880324",
            user_un="합성대학교",
            user_sp="방사선학과",
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
            )
        with self.assertRaises(ValidationError):
            LearnerSignup(
                user_id="future@example.test",
                user_pw="pass-1234",
                user_name="학습",
                user_birth="2999-01-01",
                user_un="학교",
                user_sp="전공",
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

        cls.h = Harness()
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
        cls.h.close()

    def test_signup_saves_profile_and_login_has_no_password_fields(self) -> None:
        created = self.client.post(
            "/signup",
            json={
                "user_id": "signup-new@example.test",
                "user_pw": PASSWORD,
                "user_name": "가입",
                "user_birth": "2000-05-05",
                "user_un": "합성대학교",
                "user_sp": "의학과",
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
