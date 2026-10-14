"""실제 임시 DB로 계정찾기·탈퇴 회원 제외·해시 저장·로그인을 검증한다."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from db import get_db
from models.userModel import UserModel
from routers.account import router
from services import accountServices
from schemas.userSchemas import UserLogin
from services.userServices import login
from utils.password import hash_password, verify_password


IDENTITY = {"user_id": "learner", "user_name": "테스트", "user_birth": "2000-01-02"}


@pytest.fixture
def recovery():
    accountServices.ip_limiter.reset()
    accountServices.identity_limiter.reset()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    UserModel.__table__.create(engine)
    with Session(engine) as db:
        for idx, user_id, deleted in [(1, "learner", "N"), (2, "deleted", "Y")]:
            db.add(UserModel(idx=idx, user_id=user_id, user_name="테스트", user_birth="2000-01-02",
                             user_pw=hash_password("before-change"), user_un="학교", user_sp="전공",
                             del_yn=deleted, state="N", mb_level=1))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    app.mount("/assets", StaticFiles(directory=Path(__file__).resolve().parents[1] / "assets"), name="assets")

    def test_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def test_page_and_assets(recovery):
    client, _ = recovery
    response = client.get("/find-account")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert 'id="resetSection"' in response.text
    assert client.get("/assets/js/find-account.js").status_code == 200
    assert client.get("/assets/css/find-account.css").status_code == 200


def test_find_id_excludes_deleted_and_returns_no_password(recovery):
    client, _ = recovery
    response = client.post("/find-id", json={k: IDENTITY[k] for k in ("user_name", "user_birth")})
    assert response.json() == {"ok": True, "user_id": "le***", "user_ids": ["le***"]}
    assert response.headers["cache-control"] == "no-store"
    assert "user_pw" not in response.text


def test_same_name_and_birth_can_return_multiple_ids(recovery):
    client, engine = recovery
    with Session(engine) as db:
        db.add(UserModel(idx=3, user_id="learner-two", user_name="테스트", user_birth="2000-01-02",
                         user_pw=hash_password("other-secret"), user_un="학교", user_sp="전공"))
        db.commit()
    response = client.post("/find-id", json={"user_name": "테스트", "user_birth": "2000-01-02"})
    assert response.json()["user_ids"] == ["le***", "le***"]
    assert response.json()["user_id"] is None


@pytest.mark.parametrize("field,value", [("user_id", "missing"), ("user_name", "다른이름"),
                                         ("user_birth", "2000-01-03"), ("user_id", "deleted")])
def test_identity_must_match_all_fields(recovery, field, value):
    client, _ = recovery
    body = dict(IDENTITY, **{field: value})
    assert client.post("/find-pw/verify", json=body).status_code == 404
    response = client.post("/find-pw", json=dict(body, user_pw="after-change", user_pw_confirm="after-change"))
    assert response.status_code == 404


def test_reset_hashes_password_and_existing_login_accepts_it(recovery):
    client, engine = recovery
    assert client.post("/find-pw/verify", json=IDENTITY).json() == {"ok": True}
    response = client.post("/find-pw", json=dict(IDENTITY, user_pw="after-change", user_pw_confirm="after-change"))
    assert response.status_code == 200
    assert "after-change" not in response.text
    with Session(engine) as db:
        user = db.get(UserModel, 1)
        assert user.user_pw != "after-change"
        assert verify_password("after-change", user.user_pw)
        assert user.updated_at
        assert login(db, UserLogin(user_id="learner", user_pw="after-change")) is not None
        assert login(db, UserLogin(user_id="learner", user_pw="before-change")) is None


def test_reset_rechecks_identity_if_account_deleted_after_verification(recovery):
    client, engine = recovery
    assert client.post("/find-pw/verify", json=IDENTITY).status_code == 200
    with Session(engine) as db:
        db.get(UserModel, 1).del_yn = "Y"
        db.commit()
    assert client.post("/find-pw", json=dict(IDENTITY, user_pw="changed", user_pw_confirm="changed")).status_code == 404


def test_password_mismatch_does_not_change_database(recovery):
    client, engine = recovery
    response = client.post("/find-pw", json=dict(IDENTITY, user_pw="changed", user_pw_confirm="different"))
    assert response.status_code == 400
    with Session(engine) as db:
        assert verify_password("before-change", db.get(UserModel, 1).user_pw)


@pytest.mark.parametrize("birth", ["2000-02-30", "20000102", "2999-01-01", "2000-13-01"])
def test_invalid_birth_rejected(recovery, birth):
    client, _ = recovery
    assert client.post("/find-id", json={"user_name": "테스트", "user_birth": birth}).status_code == 422


def test_validation_never_echoes_password(recovery, caplog):
    client, _ = recovery
    secret = "민감비밀번호" * 20
    response = client.post("/find-pw", json=dict(IDENTITY, user_pw=secret, user_pw_confirm=secret))
    assert response.status_code == 422
    assert secret not in response.text
    assert secret not in caplog.text


def test_password_whitespace_is_preserved(recovery):
    client, engine = recovery
    password = "  spaced password  "
    assert client.post("/find-pw", json=dict(IDENTITY, user_pw=password, user_pw_confirm=password)).status_code == 200
    with Session(engine) as db:
        assert verify_password(password, db.get(UserModel, 1).user_pw)
        assert not verify_password(password.strip(), db.get(UserModel, 1).user_pw)



@pytest.mark.parametrize("user_id,masked", [("learner@gmail.com", "le***@gmail.com"),
                                            ("ab@x.com", "a***@x.com"), ("a@x.com", "a***@x.com"),
                                            ("learner", "le***")])
def test_mask_user_id(user_id, masked):
    assert accountServices.mask_user_id(user_id) == masked


def test_find_id_never_returns_full_email(recovery):
    client, engine = recovery
    with Session(engine) as db:
        db.add(UserModel(idx=4, user_id="someone@gmail.com", user_name="메일", user_birth="1999-09-09",
                         user_pw=hash_password("secret"), user_un="학교", user_sp="전공"))
        db.commit()
    response = client.post("/find-id", json={"user_name": "메일", "user_birth": "1999-09-09"})
    assert response.json()["user_ids"] == ["so***@gmail.com"]
    assert "someone" not in response.text


def test_identity_attempts_are_limited(recovery):
    client, _ = recovery
    wrong = dict(IDENTITY, user_name="다른이름")
    for _ in range(accountServices.identity_limiter.max_attempts):
        assert client.post("/find-pw/verify", json=wrong).status_code == 404
    # 제한에 걸리면 정답을 넣어도 막고, 비밀번호 변경도 막는다.
    response = client.post("/find-pw/verify", json=IDENTITY)
    assert response.status_code == 429
    assert response.json()["ok"] is False
    response = client.post("/find-pw", json=dict(IDENTITY, user_pw="changed", user_pw_confirm="changed"))
    assert response.status_code == 429
    # 다른 계정은 영향을 받지 않는다.
    assert client.post("/find-id", json={"user_name": "테스트", "user_birth": "2000-01-02"}).status_code == 200


def test_ip_attempts_are_limited(recovery):
    client, _ = recovery
    for i in range(accountServices.ip_limiter.max_attempts):
        client.post("/find-pw/verify", json=dict(IDENTITY, user_id=f"user{i}"))
    assert client.post("/find-id", json={"user_name": "테스트", "user_birth": "2000-01-02"}).status_code == 429


def test_rate_limiter_window_expires():
    from utils.rate_limit import RateLimiter
    limiter = RateLimiter(max_attempts=2, window_seconds=60)
    assert limiter.hit("k", now=0) and limiter.hit("k", now=1)
    assert not limiter.hit("k", now=59)
    assert limiter.hit("k", now=61)
