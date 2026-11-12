"""내 정보 수정·비밀번호 변경(GET /me/settings, PATCH /me/profile, POST /me/password) 시험. ACCOUNT-FIND-MYPAGE-20261105 / FOLLOWUP2-20261106.

임시 내장 PostgreSQL(pgserver) 하나와 합성 회원만 쓴다. 프로젝트 DB·실제 회원·.env 와 무관하며 끝나면 지운다.
행 잠금(FOR UPDATE)·동시 요청·쓰기 대상 칼럼은 실제 PostgreSQL 로 확인한다. 합성 민감값(SENTINEL)이 응답·로그에 새는지도 본다.
"""
import json
import logging
import shutil
import tempfile
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from db import Base, get_db
from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from models.userModel import UserModel
from routers.account import router as account_router
from routers.user import router as user_router
from schemas import accountSettingsSchemas as S
from services import accountSettingsServices as svc
from utils.password import hash_password, verify_password

ORIGIN = {"Origin": "http://testserver"}
PW = "Pass-1234"
SETTINGS_KEYS = {"ok", "profile", "profile_version"}
PROFILE_KEYS = {"idx", "user_id", "user_name", "user_un", "user_sp"}


@pytest.fixture(scope="module")
def pg():
    import pgserver

    tmp = Path(tempfile.mkdtemp(prefix="account_settings_pg_"))
    server = pgserver.get_server(tmp / "pg", cleanup_mode="stop")
    engine = create_engine(server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1), pool_size=10)
    Base.metadata.create_all(engine, tables=[UserModel.__table__, StudyModel.__table__, ReviewNodeModel.__table__])
    yield engine
    engine.dispose()
    server.cleanup()
    shutil.rmtree(tmp, ignore_errors=True)


def _users(pw_hash):
    def member(idx, uid, name, school, major, **kw):
        return UserModel(idx=idx, user_id=uid, user_name=name, user_birth="1999-09-09", user_pw=pw_hash, user_un=school, user_sp=major,
                         del_yn=kw.pop("del_yn", "N"), state=kw.pop("state", "N"), mb_level=kw.pop("mb_level", 1), updated_at=kw.pop("updated_at", "2026-11-01 09:00:00.000"))
    return [
        member(1, "a@example.test", "에이", "가학교", "나전공"),
        member(2, "b@example.test", "비", "B학교", "B전공"),
        member(3, "gone@example.test", "탈퇴", "x", "y", del_yn="Y"),
        member(4, "admin-level@example.test", "관리", "학교", "전공", mb_level=9),
    ]


@pytest.fixture(scope="module")
def pw_hash():
    return hash_password(PW)


@pytest.fixture
def env(pg, pw_hash, monkeypatch):
    svc.learner_limiter.reset()
    svc.ip_limiter.reset()
    with pg.begin() as conn:
        conn.execute(text("TRUNCATE review_node, study, user_member RESTART IDENTITY"))
    with Session(pg) as db:
        db.add_all(_users(pw_hash))
        db.commit()
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(user_router)
    app.include_router(account_router)

    @app.post("/_set")
    async def _set(request: Request):
        body = await request.json()
        request.session.clear()
        request.session.update(body)
        return {"ok": True}

    @app.get("/_keys")
    def _keys(request: Request):
        return sorted(request.session.keys())

    def test_db():
        with Session(pg) as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    statements: list[str] = []
    listener = lambda conn, cur, stmt, params, ctx, many: statements.append(stmt)  # noqa: E731
    event.listen(pg, "before_cursor_execute", listener)
    with TestClient(app) as client:
        client.statements = statements
        client.engine = pg
        client.app_ref = app
        client.login = lambda idx=1, verified=True: client.post("/_set", json={"learner": {"idx": idx, "email_verified": verified}})
        yield client
    event.remove(pg, "before_cursor_execute", listener)


def row(env, idx=1):
    with Session(env.engine) as db:
        db.expire_all()
        u = db.get(UserModel, idx)
        return {c.name: getattr(u, c.name) for c in UserModel.__table__.columns}


def settings(env):
    res = env.get("/me/settings")
    assert res.status_code == 200, res.text
    return res.json()


def payload(env, **over):
    cur = settings(env)
    p = {"user_name": "새이름", "user_un": "새학교", "user_sp": "새전공", "current_password": PW, "expected_version": cur["profile_version"]}
    p.update(over)
    return p


def patch(env, body, headers=ORIGIN):
    return env.patch("/me/profile", json=body, headers=headers)


def change_pw(env, current=PW, new="New-pass-9", confirm=None, headers=ORIGIN):
    return env.post("/me/password", json={"current_password": current, "new_password": new, "new_password_confirm": new if confirm is None else confirm}, headers=headers)


# ── 인증·권한 (C03) ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("method,path,body", [("get", "/me/settings", None), ("patch", "/me/profile", {"user_name": "a", "user_un": "b", "user_sp": "c", "current_password": PW, "expected_version": "0" * 64}),
                                              ("post", "/me/password", {"current_password": PW, "new_password": "abcd", "new_password_confirm": "abcd"})])
def test_no_session_is_401_no_store(env, method, path, body):
    res = getattr(env, method)(path, headers=ORIGIN, **({"json": body} if body else {}))
    assert res.status_code == 401 and res.headers["cache-control"] == "no-store"
    assert res.json() == {"ok": False, "detail": "로그인이 필요합니다."}


@pytest.mark.parametrize("learner", [None, "A", [], {}, {"idx": "1", "email_verified": True}, {"idx": True, "email_verified": True}, {"idx": 0, "email_verified": True},
                                     {"idx": -1, "email_verified": True}, {"idx": 1.0, "email_verified": True}])
def test_malformed_learner_is_401(env, learner):
    env.post("/_set", json={"learner": learner})
    assert env.get("/me/settings").status_code == 401
    assert change_pw(env).status_code == 401


def test_admin_only_and_guest_are_not_learners(env):
    env.post("/_set", json={"user": {"idx": 1, "user_id": "a@example.test"}})
    assert env.get("/me/settings").status_code == 401
    env.post("/_set", json={})
    env.cookies.set("mediscan_knee_guest", "guest-token")
    assert env.get("/me/settings", headers={"X-User-Idx": "1", "Authorization": "Bearer x"}).status_code == 401
    assert row(env)["user_pw"] and verify_password(PW, row(env)["user_pw"])


def test_deleted_member_is_401_and_unapproved_is_403(env):
    env.login(3)
    assert env.get("/me/settings").status_code == 401
    for flag in (False, None, "true", 1):
        env.post("/_set", json={"learner": {"idx": 1, "email_verified": flag}})
        for res in (env.get("/me/settings"), patch(env, {"user_name": "a", "user_un": "b", "user_sp": "c", "current_password": PW, "expected_version": "0" * 64}), change_pw(env)):
            assert res.status_code == 403 and res.headers["cache-control"] == "no-store"
    assert row(env)["user_name"] == "에이"


@pytest.mark.parametrize("query", ["user_idx=2", "idx=2", "USER_ID=b", "uid=2", "id=2"])
def test_member_selection_query_is_400_everywhere(env, query):
    env.login(1)
    version = settings(env)["profile_version"]
    assert env.get(f"/me/settings?{query}").status_code == 400
    body = {"user_name": "x", "user_un": "y", "user_sp": "z", "current_password": PW, "expected_version": version}
    assert env.patch(f"/me/profile?{query}", json=body, headers=ORIGIN).status_code == 400
    assert env.post(f"/me/password?{query}", json={"current_password": PW, "new_password": "abcd", "new_password_confirm": "abcd"}, headers=ORIGIN).status_code == 400
    assert row(env)["user_name"] == "에이" and row(env, 2)["user_name"] == "비"


# ── 출처 검사 (C04) ───────────────────────────────────────────────────────────
ORIGINS = [
    {}, {"Origin": "null"}, {"Origin": "https://testserver"}, {"Origin": "http://evil.example"}, {"Origin": "http://testserver:9999"}, {"Origin": "http://sub.testserver"},
    {"Origin": "http://testserver/path"}, {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://evil.example", "X-Forwarded-Host": "testserver", "X-Forwarded-Proto": "http"},
]


@pytest.mark.parametrize("headers", ORIGINS)
def test_write_origin_matrix_is_403_and_writes_nothing(env, headers):
    env.login(1)
    body = payload(env)
    before = row(env)
    assert patch(env, body, headers).status_code == 403
    res = change_pw(env, headers=headers)
    assert res.status_code == 403 and res.headers["cache-control"] == "no-store"
    assert row(env) == before
    assert "learner" in env.get("/_keys").json()


def test_same_origin_passes_and_get_does_not_need_origin(env):
    env.login(1)
    assert env.get("/me/settings").status_code == 200
    assert patch(env, payload(env)).status_code == 200


# ── 읽기 (P01) ────────────────────────────────────────────────────────────────
def test_get_settings_shape_and_no_secrets(env):
    env.login(1)
    res = env.get("/me/settings")
    body = res.json()
    assert set(body) == SETTINGS_KEYS and set(body["profile"]) == PROFILE_KEYS and body["ok"] is True
    assert body["profile"] == {"idx": 1, "user_id": "a@example.test", "user_name": "에이", "user_un": "가학교", "user_sp": "나전공"}
    for leak in ("1999-09-09", "$2b$", "user_pw", "mb_level", "birth", "에이" + "가학교"):
        assert leak not in res.text
    version = body["profile_version"]
    assert len(version) == 64 and all(c in "0123456789abcdef" for c in version) and "에이" not in version
    assert env.get("/me").json()["profile"] == body["profile"]        # 기존 /me 응답은 그대로


def test_version_changes_with_any_profile_change_even_by_admin_update(env):
    env.login(1)
    v0 = settings(env)["profile_version"]
    assert settings(env)["profile_version"] == v0                      # 값이 같으면 같음
    with Session(env.engine) as db:
        db.execute(text("UPDATE user_member SET user_sp = '관리자가바꿈' WHERE idx = 1"))   # updated_at 은 그대로 — 값 차이만으로도 달라져야 한다
        db.commit()
    assert settings(env)["profile_version"] != v0


# ── 프로필 수정 (P02, P03) ────────────────────────────────────────────────────
def test_patch_updates_exactly_three_fields_and_updated_at(env):
    env.login(1)
    before, other_before = row(env), row(env, 2)
    old_version = settings(env)["profile_version"]
    res = patch(env, payload(env))
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    body = res.json()
    assert set(body) == SETTINGS_KEYS and body["profile"]["user_name"] == "새이름" and body["profile_version"] != old_version
    after = row(env)
    changed = {k for k in after if after[k] != before[k]}
    assert changed == {"user_name", "user_un", "user_sp", "updated_at"}
    assert row(env, 2) == other_before and after["user_pw"] == before["user_pw"] and after["mb_level"] == 1 and after["user_birth"] == "1999-09-09"
    assert settings(env) == body                                          # 다시 읽어도 같은 값·같은 버전
    assert env.get("/me").json()["profile"]["user_name"] == "새이름"


def test_patch_sql_writes_only_allowed_columns_with_row_lock(env):
    env.login(1)
    body = payload(env)
    env.statements.clear()
    assert patch(env, body).status_code == 200
    updates = [s for s in env.statements if s.lstrip().upper().startswith("UPDATE")]
    assert len(updates) == 1 and "user_member" in updates[0]
    set_part = updates[0].split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    cols = {c.split("=")[0].strip() for c in set_part.split(",")}
    assert cols <= {"user_name", "user_un", "user_sp", "updated_at"} and {"user_name", "user_un", "user_sp"} <= cols
    assert any("FOR UPDATE" in s.upper() for s in env.statements)
    assert not [s for s in env.statements if s.lstrip().upper().split(None, 1)[0] in ("INSERT", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE")]


def test_wrong_current_password_is_400_and_writes_nothing(env):
    env.login(1)
    before = row(env)
    res = patch(env, payload(env, current_password="Wrong-pass-0"))
    assert res.status_code == 400 and res.json() == {"ok": False, "detail": "현재 비밀번호가 맞지 않습니다."}
    assert row(env) == before


@pytest.mark.parametrize("extra", [{"user_idx": 2}, {"user_id": "x@y"}, {"user_birth": "2000-01-01"}, {"mb_level": 10}, {"state": "S"}, {"del_yn": "Y"}, {"idx": 2}, {"email": "x@y.z"}, {"answers": []}])
def test_unknown_or_forbidden_fields_are_422_and_write_nothing(env, extra):
    env.login(1)
    before = row(env)
    res = patch(env, {**payload(env), **extra})
    assert res.status_code == 422 and res.headers["cache-control"] == "no-store"
    assert row(env) == before


@pytest.mark.parametrize("missing", ["user_name", "user_un", "user_sp", "current_password", "expected_version"])
def test_missing_field_is_422(env, missing):
    env.login(1)
    body = payload(env)
    body.pop(missing)
    assert patch(env, body).status_code == 422
    assert row(env)["user_name"] == "에이"


@pytest.mark.parametrize("over", [
    {"user_name": None}, {"user_name": 5}, {"user_name": ["a"]}, {"user_name": ""}, {"user_name": "   "}, {"user_name": "열한글자이름이에요요요요"}, {"user_name": "a\nb"}, {"user_name": "a\tb"}, {"user_name": "a\x00b"}, {"user_name": "a b"},
    {"user_un": ""}, {"user_un": "가" * 21}, {"user_un": "학교\n"}, {"user_un": 3}, {"user_sp": " "}, {"user_sp": "나" * 21}, {"user_sp": None},
    {"current_password": ""}, {"current_password": "가" * 25}, {"current_password": 1234}, {"expected_version": "abc"}, {"expected_version": "G" * 64}, {"expected_version": 5},
])
def test_invalid_values_are_422_without_echo_and_write_nothing(env, over):
    env.login(1)
    before = row(env)
    res = patch(env, payload(env, **over))
    assert res.status_code == 422, over
    assert row(env) == before
    for value in over.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in res.text


def test_boundaries_trim_and_text_preserved(env):
    env.login(1)
    res = patch(env, payload(env, user_name="  가나다라마바사아자차  ", user_un="가" * 20, user_sp="  <b>전공</b> a  b  "))
    assert res.status_code == 200
    p = res.json()["profile"]
    assert p["user_name"] == "가나다라마바사아자차" and p["user_un"] == "가" * 20 and p["user_sp"] == "<b>전공</b> a  b"   # strip 만, 내부 공백·HTML 글자 그대로
    assert patch(env, payload(env, user_name="가" * 11)).status_code == 422


def test_unchanged_values_still_need_password_and_do_not_write(env):
    env.login(1)
    cur = settings(env)
    before = row(env)
    res = patch(env, {"user_name": "에이", "user_un": "가학교", "user_sp": "나전공", "current_password": PW, "expected_version": cur["profile_version"]})
    assert res.status_code == 200 and res.json() == cur and row(env) == before
    assert patch(env, {"user_name": "에이", "user_un": "가학교", "user_sp": "나전공", "current_password": "Wrong-pass-0", "expected_version": cur["profile_version"]}).status_code == 400


# ── 충돌·동시성 (P04) ────────────────────────────────────────────────────────
def test_stale_version_is_409_and_does_not_overwrite(env):
    env.login(1)
    body_a = payload(env, user_name="A가")
    body_b = payload(env, user_name="B가")            # 같은 버전에서 만든 두 요청
    assert patch(env, body_a).status_code == 200
    res = patch(env, body_b)
    assert res.status_code == 409 and res.json() == {"ok": False, "detail": "다른 곳에서 정보가 바뀌었습니다. 다시 불러온 뒤 확인해 주세요."}
    assert row(env)["user_name"] == "A가"
    assert patch(env, payload(env, user_name="B가")).status_code == 200      # 새로 불러오면 다시 저장할 수 있다


def test_conflict_is_checked_before_password_and_after_admin_change(env):
    env.login(1)
    body = payload(env)
    with Session(env.engine) as db:
        db.execute(text("UPDATE user_member SET user_un = '관리자변경', updated_at = '2026-11-06 10:00:00.000' WHERE idx = 1"))
        db.commit()
    res = patch(env, {**body, "current_password": "Wrong-pass-0"})
    assert res.status_code == 409                       # 충돌이 먼저(비밀번호 맞는지 알려 주지 않음)
    assert row(env)["user_un"] == "관리자변경"


def test_concurrent_patches_one_wins_one_conflicts(env):
    env.login(1)
    version = settings(env)["profile_version"]
    results: list[int] = []
    barrier = threading.Barrier(2)

    def worker(name):
        with TestClient(env.app_ref) as c:
            c.post("/_set", json={"learner": {"idx": 1, "email_verified": True}})
            barrier.wait()
            body = {"user_name": name, "user_un": "동시", "user_sp": "수정", "current_password": PW, "expected_version": version}
            results.append(c.patch("/me/profile", json=body, headers=ORIGIN).status_code)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("동시A", "동시B")]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert sorted(results) == [200, 409]
    assert row(env)["user_name"] in ("동시A", "동시B")


def test_concurrent_password_change_with_old_password_changes_only_once(env):
    env.login(1)
    results: list[int] = []
    barrier = threading.Barrier(2)

    def worker(new):
        with TestClient(env.app_ref) as c:
            c.post("/_set", json={"learner": {"idx": 1, "email_verified": True}})
            barrier.wait()
            results.append(c.post("/me/password", json={"current_password": PW, "new_password": new, "new_password_confirm": new}, headers=ORIGIN).status_code)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("First-pass-1", "Second-pass-2")]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert sorted(results) == [200, 400]
    final = row(env)["user_pw"]
    assert sum(verify_password(p, final) for p in ("First-pass-1", "Second-pass-2")) == 1 and not verify_password(PW, final)


# ── 비밀번호 변경 (P05, P06) ─────────────────────────────────────────────────
def test_password_change_success_ends_only_this_learner_session(env):
    env.post("/_set", json={"learner": {"idx": 1, "email_verified": True}, "user": {"idx": 99, "user_id": "admin-sentinel"}})
    env.cookies.set("mediscan_knee_guest", "guest-token")
    before = row(env)
    res = change_pw(env, new="  New pass 9  ")           # 앞뒤 공백도 그대로 비밀번호의 일부
    assert res.status_code == 200 and res.json() == {"ok": True, "password_changed": True, "session_ended": True}
    assert res.headers["cache-control"] == "no-store"
    after = row(env)
    assert {k for k in after if after[k] != before[k]} == {"user_pw", "updated_at"}
    assert verify_password("  New pass 9  ", after["user_pw"]) and not verify_password("New pass 9", after["user_pw"]) and not verify_password(PW, after["user_pw"])
    assert "$2b$" not in res.text and "New pass" not in res.text
    assert env.get("/_keys").json() == ["user"]            # learner 키만 사라지고 관리자 세션은 그대로
    assert env.cookies.get("mediscan_knee_guest") == "guest-token"
    assert env.get("/me/settings").status_code == 401 and env.get("/me").status_code == 401
    assert row(env, 2)["user_pw"] == before["user_pw"]


def test_password_change_sql_writes_only_hash_and_updated_at(env):
    env.login(1)
    env.statements.clear()
    assert change_pw(env).status_code == 200
    updates = [s for s in env.statements if s.lstrip().upper().startswith("UPDATE")]
    assert len(updates) == 1
    cols = {c.split("=")[0].strip() for c in updates[0].split(" SET ", 1)[1].split(" WHERE ", 1)[0].split(",")}
    assert cols == {"user_pw", "updated_at"} and any("FOR UPDATE" in s.upper() for s in env.statements)


def test_password_change_rejections_write_nothing(env):
    env.login(1)
    before = row(env)
    assert change_pw(env, current="Wrong-pass-0").status_code == 400
    assert change_pw(env, confirm="Different-1").status_code == 400
    assert change_pw(env, new=PW).status_code == 400                                   # 현재와 같음
    assert row(env) == before and "learner" in env.get("/_keys").json()


@pytest.mark.parametrize("new,code", [("abc", 422), ("abcd", 200), ("a" * 72, 200), ("a" * 73, 422), ("가" * 24, 200), ("가" * 25, 422), ("", 422)])
def test_new_password_boundaries(env, new, code):
    env.login(1)
    assert change_pw(env, new=new).status_code == code


@pytest.mark.parametrize("body", [{"current_password": PW, "new_password": "abcd"}, {"current_password": PW, "new_password": "abcd", "new_password_confirm": "abcd", "user_idx": 2},
                                  {"current_password": 1, "new_password": "abcd", "new_password_confirm": "abcd"}, {"current_password": "가" * 25, "new_password": "abcd", "new_password_confirm": "abcd"}])
def test_password_body_shape_is_422(env, body):
    env.login(1)
    before = row(env)
    assert env.post("/me/password", json=body, headers=ORIGIN).status_code == 422
    assert row(env) == before


def test_new_login_works_with_new_password_only(env):
    env.login(1)
    assert change_pw(env, new="Brand-new-5").status_code == 200
    final = row(env)["user_pw"]
    assert verify_password("Brand-new-5", final) and not verify_password(PW, final)


# ── 응답·로그 비반사 (C05) ────────────────────────────────────────────────────
def test_no_echo_of_passwords_or_inputs_in_errors_and_logs(env, caplog):
    env.login(1)
    sentinel_pw = "SENT-wrong-pw-ZZ9"
    long_name = "SENTINEL-NAME-TOO-LONG"
    with caplog.at_level(logging.DEBUG):
        r1 = patch(env, payload(env, current_password=sentinel_pw))
        r2 = patch(env, payload(env, user_name=long_name))
        r3 = change_pw(env, current=sentinel_pw, new="SENT-new-pw-ZZ8")
        r4 = env.post("/me/password", content="{not json SENT-RAW", headers={**ORIGIN, "Content-Type": "application/json"})
    for res in (r1, r2, r3, r4):
        for secret in (sentinel_pw, long_name, "SENT-new-pw-ZZ8", "SENT-RAW"):
            assert secret not in res.text
    assert sentinel_pw not in caplog.text and "SENT-new-pw-ZZ8" not in caplog.text and long_name not in caplog.text


def test_unexpected_error_is_fixed_500_without_leak_and_rolls_back(env, monkeypatch, caplog):
    env.login(1)
    body = payload(env)
    before = row(env)

    def boom(*a, **k):
        raise RuntimeError("SENTINEL-DB-DETAIL postgresql://user:pw@host/db")

    monkeypatch.setattr(svc.repositories, "apply_profile", boom)
    with caplog.at_level(logging.ERROR):
        res = patch(env, body)
    assert res.status_code == 500 and res.json() == {"ok": False, "detail": "서버 처리 중 문제가 생겼습니다."} and res.headers["cache-control"] == "no-store"
    assert "SENTINEL" not in res.text and "SENTINEL" not in caplog.text and "RuntimeError" in caplog.text
    assert row(env) == before


def test_commit_failure_rolls_back_everything(env, monkeypatch):
    env.login(1)
    body = payload(env)
    before, other = row(env), row(env, 2)
    real_commit = Session.commit

    def failing_commit(self):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(Session, "commit", failing_commit)
    assert patch(env, body).status_code == 500
    assert change_pw(env).status_code == 500
    monkeypatch.setattr(Session, "commit", real_commit)
    assert row(env) == before and row(env, 2) == other
    assert "learner" in env.get("/_keys").json()           # 실패했으니 세션 유지


# ── 시도 제한 (P08) ───────────────────────────────────────────────────────────
def test_attempt_limit_5_per_learner_counts_wrong_passwords_and_is_not_reset_by_success(env):
    env.login(1)
    for _ in range(4):
        assert patch(env, payload(env, current_password="Wrong-pass-0")).status_code == 400
    assert patch(env, payload(env)).status_code == 200                       # 5번째(성공) — 성공해도 예산은 초기화되지 않는다
    res = patch(env, payload(env, user_name="여섯째"))
    assert res.status_code == 429 and res.headers["cache-control"] == "no-store" and "10분" in res.json()["detail"]
    assert change_pw(env).status_code == 429                                  # 프로필·비밀번호 공통 예산
    assert row(env)["user_name"] == "새이름"
    env.login(2)
    assert patch(env, payload(env)).status_code == 200                        # 다른 회원은 별도 예산
    assert env.get("/me/settings").status_code == 200                         # 조회는 제한 대상이 아님


def test_attempt_limit_window_boundary_uses_clock(env, monkeypatch):
    import utils.rate_limit as rl

    clock = {"t": 1000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: clock["t"])
    env.login(1)
    for _ in range(5):
        assert change_pw(env, current="Wrong-pass-0").status_code == 400
    clock["t"] = 1000.0 + 599
    assert change_pw(env, current="Wrong-pass-0").status_code == 429
    clock["t"] = 1000.0 + 601
    assert change_pw(env, current="Wrong-pass-0").status_code == 400          # 600초가 지나면 다시 허용


def test_attempt_limit_30_per_ip_across_members(env):
    env.login(1)
    for _ in range(30):
        svc.ip_limiter.hit("ip:testclient")
    assert patch(env, payload(env)).status_code == 429
    assert row(env)["user_name"] == "에이"


def test_changing_other_values_does_not_buy_new_budget(env):
    env.login(1)
    for i in range(5):
        assert patch(env, payload(env, user_name=f"이름{i}", current_password="Wrong-pass-0")).status_code == 400
    assert patch(env, payload(env, user_name="또다른", user_un="다른학교", user_sp="다른전공")).status_code == 429


# ── 기존 계약 보존 (C06) ─────────────────────────────────────────────────────
def test_existing_me_and_logout_contracts_are_unchanged(env):
    env.login(1)
    me = env.get("/me").json()
    assert set(me) == {"ok", "profile", "recent_learning"} and set(me["profile"]) == PROFILE_KEYS
    assert env.post("/logout", headers=ORIGIN).json() == {"ok": True}
    assert env.get("/me").status_code == 401


# ── DTO 단위 ─────────────────────────────────────────────────────────────────
def test_schema_rules_directly():
    ok = S.ProfilePatch(user_name=" 이름 ", user_un="학교", user_sp="전공", current_password=" pw ", expected_version="a" * 64)
    assert (ok.user_name, ok.current_password) == ("이름", " pw ")                 # 이름은 strip, 비밀번호는 그대로
    with pytest.raises(Exception):
        S.ProfilePatch(user_name="이름", user_un="학교", user_sp="전공", current_password="pw", expected_version="a" * 64, mb_level=3)
    with pytest.raises(Exception):
        S.PasswordChange(current_password="pw", new_password="abc", new_password_confirm="abc")
    assert S.PasswordChangedResponse().model_dump() == {"ok": True, "password_changed": True, "session_ended": True}
    assert json.dumps(S.SettingsResponse.model_json_schema())               # 응답 스키마 생성 가능
