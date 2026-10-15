"""학습자 로그인(POST /login) 응답에 비밀번호(입력값·해시)가 나가지 않는지 확인한다."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from db import get_db
from models.userModel import UserModel
from routers import commonness
from utils.password import hash_password

PASSWORD = "secret-pw-1234"


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    UserModel.__table__.create(engine)
    with Session(engine) as db:
        db.add(UserModel(idx=1, user_id="learner@example.com", user_name="테스트", user_birth="2000-01-02",
                         user_pw=hash_password(PASSWORD), user_un="학교", user_sp="전공",
                         del_yn="N", state="N", mb_level=1))
        db.commit()
    app = FastAPI()
    app.include_router(commonness.router)

    def test_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as c:
        yield c
    engine.dispose()


def test_login_success_returns_profile_only(client):
    response = client.post("/login", json={"userId": "learner@example.com", "userPw": PASSWORD})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "idx": 1, "user_id": "learner@example.com",
                               "user_name": "테스트", "mb_level": 1}
    assert PASSWORD not in response.text and "$2" not in response.text


@pytest.mark.parametrize("user_id,pw", [("learner@example.com", "wrong-pw"), ("nobody@example.com", PASSWORD)])
def test_login_failure_hides_password_and_reason(client, user_id, pw):
    response = client.post("/login", json={"userId": user_id, "userPw": pw})
    assert response.status_code == 401
    # 아이디가 없든 비밀번호가 틀리든 같은 문장 (F_R_201: 원인을 짐작할 수 없게)
    assert response.json()["detail"] == "아이디 또는 비밀번호가 올바르지 않습니다."
    assert pw not in response.text and "$2" not in response.text
