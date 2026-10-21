"""
학습자 로그인(POST /login)이 학습자 세션(session["learner"])을 만들어 무릎 학습을 이을 수 있는지 확인한다.

발표용 최소 방식(사용자 결정 2026-10-13): 이메일 인증 메일 없이, 관리자(팀)가 만든 활성 회원이 로그인에 성공하면 승인된 학습자로 본다.
- 관리자 로그인이 쓰는 session["user"] 와 분리한다(서로 채우지 않는다).
- 시험 전용 로그인(/_login)을 쓰지 않고 **실제 POST /login 과 실제 무릎 라우터**를 임시 PostgreSQL 위에서 잇는다.
- 합성 계정·합성 영상·임시 승인표만 쓴다. 실제 회원·실제 DB 는 쓰지 않는다.
실행: python -m pytest tests/test_learner_login.py   (pgserver, psycopg, pillow 필요. 없으면 건너뜀)
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from db import get_db
from routers import commonness, kneeLearning
from schemas.userSchemas import UserCreate
from services import userServices as user_service

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    import PIL  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False

PASSWORD = "synthetic-pass-1234"   # 합성 비밀번호


@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg/pillow 가 없어 임시 DB 시험을 건너뜀")
class LearnerLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        cls.h = Harness()
        with cls.h.Session() as db:
            def member(user_id):
                body = UserCreate(user_id=user_id, user_pw=PASSWORD, user_name="합성", user_birth="2000-01-01", user_un="합성대", user_sp="합성과")
                return user_service.create_user(db, body).idx
            cls.member_idx = member("login-a@example.test")
            cls.other_member_idx = member("login-b@example.test")
            cls.removed_idx = member("login-gone@example.test")
            from models.userModel import UserModel
            db.get(UserModel, cls.removed_idx).del_yn = "Y"
            db.commit()

        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="login-test-secret")
        app.include_router(commonness.router)
        app.include_router(kneeLearning.router)

        @app.get("/_session")
        async def _session(request: Request):
            return dict(request.session)

        @app.post("/_admin_session")
        async def _admin_session(request: Request):
            request.session["user"] = {"idx": 999, "mb_level": 10}   # 관리자 로그인이 만드는 값 모양(시험용)
            return {"ok": True}

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

    def login(self, user_id="login-a@example.test", password=PASSWORD):
        return self.client.post("/login", json={"user_id": user_id, "user_pw": password})

    def session(self):
        return self.client.get("/_session").json()

    def task(self):
        return self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})

    def test_success_creates_a_minimal_learner_session_and_unlocks_the_knee_api(self):
        self.assertEqual(self.task().status_code, 401)                       # 로그인 전에는 막힌다
        res = self.login()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["ok"])
        self.assertEqual(self.session(), {"learner": {"idx": self.member_idx, "email_verified": True}})
        self.assertIs(self.session()["learner"]["email_verified"], True)     # 정확히 bool True
        task = self.task()
        self.assertEqual(task.status_code, 200, task.text)
        self.assertTrue(task.json()["knee_task"]["grading_available"])

    def test_login_response_does_not_expose_the_session(self):
        body = self.login().json()
        self.assertNotIn("learner", body)
        self.assertNotIn("email_verified", body)

    def test_session_holds_no_personal_data(self):
        self.login()
        dumped = str(self.session())
        for value in ("login-a@example.test", PASSWORD, "합성"):
            self.assertNotIn(value, dumped)

    def test_full_flow_real_login_then_task_then_grade_is_saved_under_that_member(self):
        self.login(user_id="login-b@example.test")
        task = self.task().json()["knee_task"]
        before = len(self.h.reviews())
        res = self.client.post("/learning/roi-grade/knee", data={
            "study_idx": str(self.h.study_idx), "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4",
            "slice_index": str(task["representative_slice_index"]), "problem_token": task["problem_token"],
        })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["graded"])
        rows = self.h.reviews()
        self.assertEqual(len(rows), before + 1)
        self.assertEqual(rows[-1].rn_u_idx, self.other_member_idx)           # 세션의 회원 번호로 저장(요청값 아님)

    def test_failed_logins_create_no_session(self):
        for user_id, password in (("login-a@example.test", "wrong-password"), ("nobody@example.test", PASSWORD),
                                  ("login-gone@example.test", PASSWORD), ("login-a@example.test", ""), ("", PASSWORD)):
            res = self.login(user_id, password)
            self.assertIn(res.status_code, (401, 422), (user_id, res.status_code))
            self.assertEqual(self.session(), {}, user_id)
            self.assertEqual(self.task().status_code, 401)

    def test_failed_login_does_not_log_out_an_existing_learner(self):
        self.login()
        self.assertEqual(self.login(password="wrong-password").status_code, 401)
        self.assertEqual(self.session()["learner"]["idx"], self.member_idx)   # 실패한 시도가 기존 세션을 지우거나 바꾸지 않는다

    def test_logging_in_as_another_member_replaces_the_learner(self):
        self.login()
        self.login(user_id="login-b@example.test")
        self.assertEqual(self.session(), {"learner": {"idx": self.other_member_idx, "email_verified": True}})

    def test_learner_login_and_admin_session_do_not_touch_each_other(self):
        self.client.post("/_admin_session")
        self.assertEqual(self.task().status_code, 401)                       # 관리자 세션만으로는 학습자가 아니다
        self.login()
        session = self.session()
        self.assertEqual(session["user"], {"idx": 999, "mb_level": 10})      # 학습자 로그인이 관리자 세션을 지우거나 바꾸지 않는다
        self.assertEqual(session["learner"]["idx"], self.member_idx)
        self.assertEqual(self.task().status_code, 200)

    def test_member_removed_after_login_is_blocked_by_the_db_check(self):
        from models.userModel import UserModel
        self.login(user_id="login-b@example.test")
        self.assertEqual(self.task().status_code, 200)
        with self.h.Session() as db:
            db.get(UserModel, self.other_member_idx).del_yn = "Y"
            db.commit()
        try:
            self.assertEqual(self.task().status_code, 401)                   # 세션이 있어도 DB 에서 삭제된 회원은 막힌다
        finally:
            with self.h.Session() as db:
                db.get(UserModel, self.other_member_idx).del_yn = "N"
                db.commit()

    def test_feature_switch_still_closes_the_knee_api_after_login(self):
        from unittest import mock
        self.login()
        with mock.patch.dict(os.environ, {"KNEE_ROI_GRADE_ENABLED": ""}):
            self.assertEqual(self.task().status_code, 503)                   # 로그인만으로는 기능이 켜지지 않는다


if __name__ == "__main__":
    unittest.main()
