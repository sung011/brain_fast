"""GET /me(마이페이지 본인 조회)와 POST /logout, 계정 API 예상 못한 오류의 응답·로그 시험. ACCOUNT-FIND-MYPAGE-20261105.

임시 SQLite·합성 회원/문제/제출만 쓴다. 프로젝트 DB·실제 이름·생년월일·비밀번호는 없다. 합성 민감값(SENTINEL)이 응답·로그에 새는지도 본다.
"""
import json
import logging
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.middleware.sessions import SessionMiddleware

from db import get_db
from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from models.userModel import UserModel
from routers.account import router as account_router
from routers.user import router as user_router
from services import accountProfileServices, accountServices
from utils.password import hash_password

SAME_ORIGIN = {"Origin": "http://testserver"}
BASE = datetime(2026, 11, 5, 10, 0, 0)
SENTINELS = ["SENTINEL-IMG", "SENTINEL-DISEASE", "SENTINEL-SUMMARY", "SENTINEL-FINDINGS", "SENTINEL-STDISEASE", "SENTINEL-STIMAGE", "1999-09-09"]
ME_KEYS = {"ok", "profile", "recent_learning"}
PROFILE_KEYS = {"idx", "user_id", "user_name", "user_un", "user_sp"}
ITEM_KEYS = {"review_idx", "study_idx", "part_label", "modal_label", "study_available", "solution", "solution_label", "created_at"}


def _review(idx, user, study, solution, at, **kw):
    return ReviewNodeModel(
        idx=idx, rn_u_idx=user, rn_s_idx=study, rn_solution=solution, created_at=at, del_yn=kw.pop("del_yn", "N"),
        rn_image="/stylesheets/assets/review/SENTINEL-IMG.png", rn_disease="SENTINEL-DISEASE",
        rn_overlay_summary="SENTINEL-SUMMARY", rn_findings='{"x":"SENTINEL-FINDINGS"}', rn_recall=0.5, rn_iou=0.4, **kw,
    )


@pytest.fixture
def env():
    accountServices.ip_limiter.reset()
    accountServices.identity_limiter.reset()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (UserModel, StudyModel, ReviewNodeModel):
        model.__table__.create(engine)
    with Session(engine) as db:
        pw = hash_password("pw-sentinel")
        db.add_all([
            UserModel(idx=1, user_id="a@example.test", user_name="에이", user_birth="1999-09-09", user_pw=pw, user_un="가학교", user_sp="나전공", del_yn="N", state="N", mb_level=1),
            UserModel(idx=2, user_id="b@example.test", user_name="비", user_birth="1999-09-09", user_pw=pw, user_un="B학교", user_sp="B전공", del_yn="N", state="N", mb_level=1),
            UserModel(idx=3, user_id="gone@example.test", user_name="탈퇴", user_birth="1999-09-09", user_pw=pw, user_un="x", user_sp="y", del_yn="Y", state="N", mb_level=1),
            UserModel(idx=4, user_id="empty@example.test", user_name=None, user_birth="1999-09-09", user_pw=pw, user_un="", user_sp="", del_yn="N", state="N", mb_level=1),
        ])
        common = dict(st_image="/SENTINEL-STIMAGE/s.png", st_disease="SENTINEL-STDISEASE", state="N")
        db.add_all([
            StudyModel(idx=11, st_part="4", st_modal="3", del_yn="N", **common),   # 무릎 MRI
            StudyModel(idx=12, st_part="1", st_modal="2", del_yn="N", **common),   # 뇌 CT
            StudyModel(idx=13, st_part="2", st_modal="1", del_yn="Y", **common),   # 삭제된 문제
        ])
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
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda conn, cur, stmt, params, ctx, many: statements.append(stmt))
    with TestClient(app) as client:
        client.statements = statements
        client.engine = engine
        yield client
    engine.dispose()


def login(client, idx=1, verified=True):
    client.post("/_set", json={"learner": {"idx": idx, "email_verified": verified}})


def add_reviews(client, rows):
    with Session(client.engine) as db:
        db.add_all(rows)
        db.commit()


# ── 인증 경계 (AC13) ─────────────────────────────────────────────────────────
def test_no_session_is_401_and_no_store(env):
    res = env.get("/me")
    assert res.status_code == 401
    assert res.headers["cache-control"] == "no-store"
    assert res.json() == {"ok": False, "detail": "로그인이 필요합니다."}


@pytest.mark.parametrize("learner", [
    None, "A", [], {}, {"idx": "1", "email_verified": True}, {"idx": True, "email_verified": True},
    {"idx": 0, "email_verified": True}, {"idx": -1, "email_verified": True}, {"idx": None, "email_verified": True},
    {"idx": 1.0, "email_verified": True}, {"email_verified": True},
])
def test_malformed_learner_session_is_401(env, learner):
    env.post("/_set", json={"learner": learner})
    assert env.get("/me").status_code == 401


def test_admin_session_only_is_not_a_learner(env):
    env.post("/_set", json={"user": {"idx": 1, "user_id": "a@example.test"}})
    assert env.get("/me").status_code == 401


def test_guest_cookie_and_storage_values_do_not_log_in(env):
    env.cookies.set("mediscan_knee_guest", "guest-token")
    assert env.get("/me", headers={"X-User-Idx": "1", "Authorization": "Bearer x"}).status_code == 401


def test_deleted_or_missing_member_is_401(env):
    for idx in (3, 999):
        login(env, idx)
        assert env.get("/me").status_code == 401, idx


@pytest.mark.parametrize("flag", [False, None, "true", 1])
def test_unapproved_learner_is_403(env, flag):
    env.post("/_set", json={"learner": {"idx": 1, "email_verified": flag}})
    res = env.get("/me")
    assert res.status_code == 403
    assert res.headers["cache-control"] == "no-store"


def test_missing_flag_is_403(env):
    env.post("/_set", json={"learner": {"idx": 1}})
    assert env.get("/me").status_code == 403


# ── 타인 선택 불가 (AC14) ─────────────────────────────────────────────────────
@pytest.mark.parametrize("query", ["user_idx=2", "idx=2", "user_id=b@example.test", "IDX=2", "id=2"])
def test_query_cannot_select_another_member(env, query):
    add_reviews(env, [_review(1, 2, 11, "C", BASE)])
    login(env, 1)
    res = env.get(f"/me?{query}")
    assert res.status_code == 400
    assert res.headers["cache-control"] == "no-store"
    assert "b@example.test" not in res.text and "B학교" not in res.text


def test_query_attempt_without_login_gives_nothing_either(env):
    res = env.get("/me?user_idx=2")
    assert res.status_code == 400
    assert "b@example.test" not in res.text


def test_a_never_sees_b_profile_or_submissions(env):
    add_reviews(env, [_review(1, 1, 11, "C", BASE), _review(2, 2, 12, "W", BASE + timedelta(minutes=1))])
    login(env, 1)
    body = env.get("/me").json()
    assert body["profile"]["user_id"] == "a@example.test"
    assert [item["review_idx"] for item in body["recent_learning"]["items"]] == [1]
    assert "b@example.test" not in json.dumps(body)
    login(env, 2)
    body = env.get("/me").json()
    assert body["profile"]["user_id"] == "b@example.test"
    assert [item["review_idx"] for item in body["recent_learning"]["items"]] == [2]


# ── 허용 필드만 (AC15) ───────────────────────────────────────────────────────
def test_response_has_only_allowed_keys_and_no_sensitive_values(env):
    add_reviews(env, [_review(1, 1, 11, "C", BASE), _review(2, 1, 13, "H", BASE + timedelta(minutes=1))])
    login(env, 1)
    res = env.get("/me")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store"
    body = res.json()
    assert set(body) == ME_KEYS
    assert set(body["profile"]) == PROFILE_KEYS
    assert body["profile"] == {"idx": 1, "user_id": "a@example.test", "user_name": "에이", "user_un": "가학교", "user_sp": "나전공"}
    assert set(body["recent_learning"]) == {"limit", "items"}
    assert body["recent_learning"]["limit"] == 5
    for item in body["recent_learning"]["items"]:
        assert set(item) == ITEM_KEYS
    for sentinel in SENTINELS:
        assert sentinel not in res.text, sentinel
    for key in ("user_pw", "user_birth", "mb_level", "rn_image", "st_image", "rn_disease", "rn_findings", "rn_overlay_summary", "rn_iou", "rn_recall", "hash"):
        assert key not in res.text, key
    assert hash_password("pw-sentinel")[:7] not in res.text   # 해시 접두


def test_empty_profile_fields_are_returned_as_empty_values(env):
    login(env, 4)
    body = env.get("/me").json()
    assert body["profile"]["user_name"] is None
    assert body["profile"]["user_un"] == "" and body["profile"]["user_sp"] == ""
    assert body["recent_learning"] == {"limit": 5, "items": []}


# ── 최근 제출 규칙 (AC16·17) ──────────────────────────────────────────────────
def test_latest_five_by_time_then_idx_desc_and_repeated_study_rows(env):
    rows = [_review(i, 1, 11, "C", BASE + timedelta(minutes=i)) for i in range(1, 8)]   # 같은 문제 반복 제출 7건
    rows.append(_review(20, 1, 12, "W", BASE + timedelta(minutes=7)))                   # 7번과 같은 시각 → idx 큰 20 이 먼저
    add_reviews(env, rows)
    login(env, 1)
    items = env.get("/me").json()["recent_learning"]["items"]
    assert [item["review_idx"] for item in items] == [20, 7, 6, 5, 4]
    assert [item["study_idx"] for item in items] == [12, 11, 11, 11, 11]               # 같은 문제도 제출마다 한 행


def test_excludes_deleted_reviews_screen_analysis_and_other_members(env):
    add_reviews(env, [
        _review(1, 1, 11, "C", BASE),
        _review(2, 1, 11, "C", BASE + timedelta(minutes=5), del_yn="Y"),     # 삭제된 제출
        _review(3, 1, None, "W", BASE + timedelta(minutes=6)),                 # 문제 없는 화면 분석 기록
        _review(4, 2, 11, "W", BASE + timedelta(minutes=7)),                   # 다른 회원
        _review(5, None, 11, "W", BASE + timedelta(minutes=8)),                # 회원 없는 기록
    ])
    login(env, 1)
    assert [item["review_idx"] for item in env.get("/me").json()["recent_learning"]["items"]] == [1]


def test_deleted_or_missing_study_keeps_the_submission_without_study_details(env):
    add_reviews(env, [_review(1, 1, 13, "C", BASE), _review(2, 1, 999, "W", BASE + timedelta(minutes=1)), _review(3, 1, 11, "H", BASE + timedelta(minutes=2))])
    login(env, 1)
    items = {item["review_idx"]: item for item in env.get("/me").json()["recent_learning"]["items"]}
    for idx in (1, 2):
        assert items[idx]["study_available"] is False
        assert items[idx]["part_label"] is None and items[idx]["modal_label"] is None
    assert items[1]["solution_label"] == "정답" and items[2]["solution_label"] == "오답"
    assert (items[3]["study_available"], items[3]["part_label"], items[3]["modal_label"]) == (True, "무릎", "MRI")


def test_result_labels_and_unknown_values_are_not_treated_as_wrong(env):
    add_reviews(env, [_review(i, 1, 11, code, BASE + timedelta(minutes=i)) for i, code in enumerate(["C", "H", "W", None, "X"], start=1)])
    login(env, 1)
    items = {item["review_idx"]: item for item in env.get("/me").json()["recent_learning"]["items"]}
    assert [items[i]["solution_label"] for i in (1, 2, 3)] == ["정답", "부분정답", "오답"]
    assert [items[i]["solution"] for i in (1, 2, 3)] == ["C", "H", "W"]
    for idx in (4, 5):
        assert items[idx]["solution"] is None and items[idx]["solution_label"] == "결과 없음"


def test_created_at_is_the_database_value_without_timezone_shift(env):
    add_reviews(env, [_review(1, 1, 11, "C", datetime(2026, 11, 5, 10, 0, 0))])
    login(env, 1)
    assert env.get("/me").json()["recent_learning"]["items"][0]["created_at"] == "2026-11-05T10:00:00"


def test_missing_time_value_is_handled():
    row = type("R", (), dict(review_idx=1, study_idx=11, solution="C", created_at=None, study_found=11, study_del_yn="N", st_part="4", st_modal="3"))()
    assert accountProfileServices.submission_dto(row).created_at is None


def test_limit_is_applied_in_sql_and_only_reads_happen(env):
    add_reviews(env, [_review(i, 1, 11, "C", BASE + timedelta(minutes=i)) for i in range(1, 30)])
    login(env, 1)
    env.statements.clear()
    assert len(env.get("/me").json()["recent_learning"]["items"]) == 5
    selects = [s for s in env.statements if s.lstrip().upper().startswith("SELECT")]
    review_queries = [s for s in selects if "review_node" in s]
    assert len(review_queries) == 1 and "LIMIT" in review_queries[0].upper()      # 전부 가져와 자르지 않는다
    assert all(s.lstrip().upper().startswith(("SELECT", "PRAGMA")) for s in env.statements), env.statements   # 쓰기 0


# ── 오류와 no-store (AC21·31) ─────────────────────────────────────────────────
def test_unexpected_error_is_fixed_500_without_leaking_values(env, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("DB failure params SENTINEL-FINDINGS a@example.test 1999-09-09")
    monkeypatch.setattr(accountProfileServices.repositories, "get_active_member", boom)
    login(env, 1)
    with caplog.at_level(logging.DEBUG):
        res = env.get("/me")
    assert res.status_code == 500
    assert res.headers["cache-control"] == "no-store"
    assert res.json() == {"ok": False, "detail": "서버 처리 중 문제가 생겼습니다."}
    assert "SENTINEL" not in res.text + caplog.text and "1999-09-09" not in res.text + caplog.text
    assert "RuntimeError" in caplog.text


def test_account_api_unexpected_error_is_fixed_500_without_input_in_response_or_log(env, monkeypatch, caplog):
    from services import accountServices as service

    def boom(*args, **kwargs):
        raise RuntimeError("params SENT-NAME 1999-09-09 secret-pw-sentinel")
    monkeypatch.setattr(service.repositories, "find_ids", boom)
    monkeypatch.setattr(service.repositories, "find_member", boom)
    monkeypatch.setattr(service.repositories, "lock_member_for_reset", boom)   # 복구 B안 reset 은 잠금 조회를 쓴다
    with caplog.at_level(logging.DEBUG):
        r1 = env.post("/find-id", json={"user_name": "SENT-NAME", "user_birth": "1999-09-09"})
        answers = [{"question_id": q, "answer": "SENT-ANSWER"} for q in ("Q01", "Q02", "Q03")]
        r2 = env.post("/find-pw/verify", json={"user_id": "a@example.test", "user_name": "SENT-NAME", "user_birth": "1999-09-09", "answers": answers})
        r3 = env.post("/find-pw", json={"user_id": "a@example.test", "user_name": "SENT-NAME", "user_birth": "1999-09-09",
                                        "user_pw": "secret-pw-sentinel", "user_pw_confirm": "secret-pw-sentinel", "answers": answers})
    for res in (r1, r2, r3):
        assert res.status_code == 500
        assert res.headers["cache-control"] == "no-store"
        assert res.json() == {"ok": False, "detail": "서버 처리 중 문제가 생겼습니다."}
        for secret in ("SENT-NAME", "1999-09-09", "secret-pw-sentinel", "SENT-ANSWER"):
            assert secret not in res.text
    for secret in ("SENT-NAME", "1999-09-09", "secret-pw-sentinel", "SENT-ANSWER"):
        assert secret not in caplog.text


# ── 로그아웃 (AC22·23·24) ─────────────────────────────────────────────────────
def test_logout_ends_only_the_learner_and_keeps_admin_and_guest(env):
    env.post("/_set", json={"learner": {"idx": 1, "email_verified": True}, "user": {"idx": 9}, "other": "keep"})
    env.cookies.set("mediscan_knee_guest", "guest-token")
    assert env.get("/me").status_code == 200
    res = env.post("/logout", headers=SAME_ORIGIN)
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert res.headers["cache-control"] == "no-store"
    assert env.get("/me").status_code == 401
    assert env.get("/_keys").json() == ["other", "user"]            # 관리자 세션 보존
    assert env.cookies.get("mediscan_knee_guest") == "guest-token"   # 게스트 쿠키 보존
    assert env.post("/logout", headers=SAME_ORIGIN).status_code == 200   # 반복 호출도 200


def test_logout_without_session_is_ok(env):
    assert env.post("/logout", headers=SAME_ORIGIN).status_code == 200


@pytest.mark.parametrize("headers", [
    {},                                                                    # Origin 없는 직접 폼/호출
    {"Origin": "http://evil.example"},                                     # 외부 Origin
    {"Origin": "http://testserver.evil.example"},
    {"Origin": "https://testserver"},                                      # scheme 다름
    {"Origin": "http://testserver:8000"},                                  # 포트 다름(다른 출처)
    {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "null"},
])
def test_logout_rejects_cross_site_and_non_browser_origins(env, headers):
    login(env, 1)
    res = env.post("/logout", headers=headers)
    assert res.status_code == 403
    assert res.headers["cache-control"] == "no-store"
    assert env.get("/me").status_code == 200                              # 거절되면 로그인은 그대로


def test_logout_trusts_no_forwarded_headers(env):
    login(env, 1)
    res = env.post("/logout", headers={"Origin": "http://evil.example", "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http"})
    assert res.status_code == 403


def test_logout_get_is_not_allowed(env):
    login(env, 1)
    assert env.get("/logout").status_code == 405
    assert env.get("/me").status_code == 200


def test_logout_does_not_touch_the_database(env):
    login(env, 1)
    env.statements.clear()
    env.post("/logout", headers=SAME_ORIGIN)
    assert env.statements == []


def test_main_app_exposes_me_and_logout():
    """main.py 는 수정하지 않았다: 기존 user/account 라우터 등록만으로 새 주소가 열린다(포함 라우터는 openapi 경로 목록으로 확인)."""
    import main
    paths = main.app.openapi()["paths"]
    assert "get" in paths["/me"] and "post" in paths["/logout"]
    assert "post" in paths["/find-id"] and "post" in paths["/find-pw"] and "get" in paths["/text"]   # 기존 주소는 그대로
