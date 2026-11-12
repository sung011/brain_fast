"""GET /me/stats(마이페이지 학습 통계, 시안 B) 시험. ACCOUNT-FIND-MYPAGE-20261105 / FOLLOWUP-20261106.

임시 내장 PostgreSQL(pgserver) 하나와 합성 회원·문제·제출만 쓴다. 프로젝트 DB·실제 회원·.env 와 무관하며, 끝나면 지운다.
집계 SQL 이 PostgreSQL 전용(AT TIME ZONE·jsonb)이라 SQLite 가 아니라 실제 PostgreSQL 로 시험한다.
고정 예제(A-1)와 시계 정책(UTC/KST/미확인)·경계·권한·읽기 전용·DTO 규칙을 본다. 응답·로그에 합성 민감값이 새는지도 본다.
"""
import json
import logging
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from db import Base, get_db
from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from models.userModel import UserModel
from routers.user import router as user_router
from schemas import accountStatsSchemas as S
from services import accountStatsServices
from utils.password import hash_password

KST_NOW = datetime.fromisoformat("2026-11-06T12:00:00+09:00")
STATS_KEYS = {"ok", "stats_version", "owner_idx", "as_of", "summary", "calendar", "regions", "quality"}


@pytest.fixture(scope="module")
def pg():
    import pgserver

    tmp = Path(tempfile.mkdtemp(prefix="account_stats_pg_"))
    server = pgserver.get_server(tmp / "pg", cleanup_mode="stop")
    engine = create_engine(server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1))
    Base.metadata.create_all(engine, tables=[UserModel.__table__, StudyModel.__table__, ReviewNodeModel.__table__])
    # 이 **임시 DB 안에서만**: '날짜 없는 과거 기록' 경계 시험(설계 A-1 9번)을 위해 created_at 의 NOT NULL·기본값을 푼다(실제 스키마·제품 DDL 은 그대로).
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE review_node ALTER COLUMN created_at DROP NOT NULL, ALTER COLUMN created_at DROP DEFAULT"))
    yield engine
    engine.dispose()
    server.cleanup()
    shutil.rmtree(tmp, ignore_errors=True)


def _rv(idx, user, study, code, at, **kw):
    """review_node 합성 행. at 은 DB 에 적힌 '원시' 시각(시간대 없음) 또는 None."""
    return ReviewNodeModel(idx=idx, rn_u_idx=user, rn_s_idx=study, rn_solution=code, created_at=at, del_yn=kw.pop("del_yn", "N"),
                           rn_image="/SENTINEL-IMG.png", rn_disease="SENTINEL-DISEASE", rn_overlay_summary="SENTINEL-SUMMARY",
                           rn_findings='{"x":"SENTINEL-FINDINGS"}', **kw)


def _dt(text_):
    return datetime.fromisoformat(text_)


def seed_users_and_studies(db):
    pw = hash_password("pw-sentinel")
    db.add_all([
        UserModel(idx=1, user_id="a@example.test", user_name="에이", user_birth="1999-09-09", user_pw=pw, user_un="가학교", user_sp="나전공", del_yn="N", state="N", mb_level=1),
        UserModel(idx=2, user_id="b@example.test", user_name="비", user_birth="1999-09-09", user_pw=pw, user_un="B", user_sp="B", del_yn="N", state="N", mb_level=1),
        UserModel(idx=3, user_id="gone@example.test", user_name="탈퇴", user_birth="1999-09-09", user_pw=pw, user_un="x", user_sp="y", del_yn="Y", state="N", mb_level=1),
        UserModel(idx=4, user_id="empty@example.test", user_name="빈", user_birth="1999-09-09", user_pw=pw, user_un="", user_sp="", del_yn="N", state="N", mb_level=1),
    ])
    common = dict(st_image="/SENTINEL-STIMAGE/s.png", st_disease="SENTINEL-STDISEASE", state="N")
    db.add_all([
        StudyModel(idx=101, st_part="1", st_modal="2", del_yn="N", **common),   # 뇌
        StudyModel(idx=102, st_part="4", st_modal="3", del_yn="N", **common),   # 무릎
        StudyModel(idx=103, st_part="2", st_modal="1", del_yn="N", **common),   # 흉부
        StudyModel(idx=104, st_part="3", st_modal="2", del_yn="N", **common),   # 복부
        StudyModel(idx=105, st_part="1", st_modal="2", del_yn="Y", **common),   # 삭제된 문제
        StudyModel(idx=107, st_part="9", st_modal="2", del_yn="N", **common),   # 잘못된 부위 코드
        # 106 은 일부러 만들지 않는다(물리 삭제/없음)
    ])


A1 = [  # 설계 A-1 고정 예제(저장 시각은 'KST 원시'). rn_s_idx 106 은 study 행이 없다
    (1, 101, "W", "2026-10-31 09:00"), (2, 101, "H", "2026-11-01 09:00"), (3, 101, "C", "2026-11-02 09:00"),
    (4, 102, "H", "2026-11-03 09:00"), (5, 102, "W", "2026-11-04 09:00"), (6, 102, "C", "2026-11-05 10:00"),
    (7, 103, "W", "2026-11-05 12:00"), (8, 103, "H", "2026-11-06 09:00"), (9, 104, "C", None),
    (10, 105, "W", "2026-11-02 10:00"), (11, 106, "H", "2026-11-06 10:00"), (12, 107, "C", "2026-11-06 11:00"),
    (13, 101, "X", "2026-11-06 11:30"),
]


def seed_a1(db, shift_hours=0):
    """A-1 예제를 넣는다. shift_hours: 원시 시각을 UTC 로 저장하는 시나리오는 -9 (KST 시각 − 9시간 = UTC 시각)."""
    for idx, study, code, at in A1:
        when = None if at is None else _dt(at) + timedelta(hours=shift_hours)
        db.add(_rv(idx, 1, study, code, when))
    db.add(_rv(20, 1, 101, "C", _dt("2026-11-06 09:00"), del_yn="Y"))          # 삭제 제출
    db.add(_rv(21, 2, 101, "C", _dt("2026-11-06 09:00")))                      # 다른 회원
    db.add(_rv(22, 1, None, "C", _dt("2026-11-06 09:00")))                     # 문제 없는 화면 분석


@pytest.fixture
def env(pg, monkeypatch):
    with pg.begin() as conn:
        conn.execute(text("TRUNCATE review_node, study, user_member RESTART IDENTITY"))
    with Session(pg) as db:
        seed_users_and_studies(db)
        db.commit()
    monkeypatch.setattr(accountStatsServices, "_now_kst", lambda: KST_NOW)
    monkeypatch.setattr(accountStatsServices, "SOURCE_TIMEZONE", "Asia/Seoul")
    monkeypatch.setattr(accountStatsServices, "DAILY_GOAL", 1)   # 설계 A-1 고정 예제는 '하루 1문제' 기준이다. 하루 5문제(실제 기본값) 시험은 아래에서 따로 주입한다
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(user_router)

    @app.post("/_set")
    async def _set(request: Request):
        body = await request.json()
        request.session.clear()
        request.session.update(body)
        return {"ok": True}

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
        client.set_user = lambda idx=1, verified=True: client.post("/_set", json={"learner": {"idx": idx, "email_verified": verified}})
        yield client
    event.remove(pg, "before_cursor_execute", listener)


def add(env, rows):
    with Session(env.engine) as db:
        db.add_all(rows)
        db.commit()


def stats(env, expect=200):
    res = env.get("/me/stats")
    assert res.status_code == expect, res.text
    assert res.headers["cache-control"] == "no-store"
    return res.json()


# ── 인증·권한 (ST02) ─────────────────────────────────────────────────────────
def test_no_session_is_401_and_no_store(env):
    res = env.get("/me/stats")
    assert res.status_code == 401 and res.headers["cache-control"] == "no-store"
    assert res.json() == {"ok": False, "detail": "로그인이 필요합니다."}


@pytest.mark.parametrize("learner", [None, "A", [], {}, {"idx": "1", "email_verified": True}, {"idx": True, "email_verified": True},
                                     {"idx": 0, "email_verified": True}, {"idx": -1, "email_verified": True}, {"idx": 1.0, "email_verified": True}])
def test_malformed_learner_is_401(env, learner):
    env.post("/_set", json={"learner": learner})
    assert env.get("/me/stats").status_code == 401


def test_admin_session_and_guest_are_not_learners(env):
    env.post("/_set", json={"user": {"idx": 1, "user_id": "a@example.test"}})
    assert env.get("/me/stats").status_code == 401
    env.post("/_set", json={})
    env.cookies.set("mediscan_knee_guest", "guest-token")
    assert env.get("/me/stats", headers={"X-User-Idx": "1", "Authorization": "Bearer x"}).status_code == 401


def test_deleted_or_missing_member_is_401(env):
    for idx in (3, 999):
        env.set_user(idx)
        assert env.get("/me/stats").status_code == 401, idx


@pytest.mark.parametrize("flag", [False, None, "true", 1])
def test_unapproved_is_403(env, flag):
    env.post("/_set", json={"learner": {"idx": 1, "email_verified": flag}})
    res = env.get("/me/stats")
    assert res.status_code == 403 and res.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("query", ["user_idx=2", "idx=2", "USER_ID=b", "uid=2", "id=2", "date=2026-01-01&user_idx=2"])
def test_member_selection_query_is_400(env, query):
    env.set_user(1)
    res = env.get(f"/me/stats?{query}")
    assert res.status_code == 400 and res.headers["cache-control"] == "no-store"


def test_period_and_timezone_queries_are_ignored(env):
    env.set_user(1)
    base = stats(env)
    for q in ("tz=Asia/Tokyo", "date=2020-01-01", "days=3", "as_of=2030-01-01T00:00:00Z", "source_timezone=UTC"):
        assert env.get(f"/me/stats?{q}").json() == base


# ── 고정 예제 A-1 (ST03~ST10) ────────────────────────────────────────────────
def test_fixed_example_a1_kst_source(env):
    with Session(env.engine) as db:
        seed_a1(db)
        db.commit()
    env.set_user(1)
    body = stats(env)
    assert set(body) == STATS_KEYS and body["owner_idx"] == 1 and body["as_of"] == "2026-11-06T12:00:00+09:00"
    s = body["summary"]
    assert (s["solved_problem_count"], s["correct_count"], s["partial_count"], s["wrong_count"]) == (7, 4, 2, 1)
    assert s["correct_rate_pct"] == 57.1 and s["studied_parts_count"] == 4
    regions = {r["code"]: r for r in body["regions"]}
    assert [r["label"] for r in body["regions"]] == ["뇌", "흉부", "복부", "무릎"]
    assert (regions["1"]["correct_rate_pct"], regions["4"]["correct_rate_pct"], regions["3"]["correct_rate_pct"], regions["2"]["correct_rate_pct"]) == (100.0, 100.0, 100.0, 0.0)
    assert all(r["problem_count"] == 1 for r in body["regions"])
    q = body["quality"]
    assert (q["ungraded_submission_count"], q["undated_graded_submission_count"], q["future_graded_submission_count"], q["unclassified_problem_count"]) == (1, 1, 0, 3)
    cal = body["calendar"]
    assert cal["status"] == "ready" and cal["reason"] is None and cal["source_timezone"] == "Asia/Seoul" and cal["timezone"] == "Asia/Seoul"
    assert [d["solved_problem_count"] for d in cal["recent7"]] == [1, 1, 2, 1, 1, 2, 3]
    assert [d["date"] for d in cal["recent7"]][0] == "2026-10-31" and [d["date"] for d in cal["recent7"]][-1] == "2026-11-06"
    assert (cal["current_streak_days"], cal["longest_streak_days"], cal["today_done"], cal["weekly_solved_problem_count"]) == (7, 7, True, 6)
    assert cal["week_start"] == "2026-11-02" and cal["week_end_exclusive"] == "2026-11-09" and cal["today"] == "2026-11-06"
    assert [d["state"] for d in cal["week_days"]] == ["learned"] * 5 + ["future"] * 2


def test_same_data_as_utc_source_is_converted_exactly(env, monkeypatch):
    """원시 시각이 UTC 로 적혀 있다면 KST = +9. 같은 순간이므로 같은 결과(원시 KST 는 +9 하지 않음)."""
    with Session(env.engine) as db:
        seed_a1(db, shift_hours=-9)
        db.commit()
    monkeypatch.setattr(accountStatsServices, "SOURCE_TIMEZONE", "UTC")
    env.set_user(1)
    cal = stats(env)["calendar"]
    assert [d["solved_problem_count"] for d in cal["recent7"]] == [1, 1, 2, 1, 1, 2, 3]
    assert (cal["current_streak_days"], cal["longest_streak_days"], cal["weekly_solved_problem_count"]) == (7, 7, 6) and cal["source_timezone"] == "UTC"


def test_wrong_source_assumption_is_not_silently_applied(env, monkeypatch):
    """KST 로 저장된 값을 UTC 라고 가정하면 날짜가 달라진다 → 기본값은 가정하지 않는다는 근거(단순히 +9 하지 않음)."""
    with Session(env.engine) as db:
        seed_a1(db)
        db.commit()
    env.set_user(1)
    right = stats(env)["calendar"]["recent7"]
    monkeypatch.setattr(accountStatsServices, "SOURCE_TIMEZONE", "UTC")
    wrong = stats(env)["calendar"]["recent7"]
    assert [d["solved_problem_count"] for d in right] != [d["solved_problem_count"] for d in wrong]


def test_unverified_source_hides_calendar_but_keeps_totals(env, monkeypatch):
    with Session(env.engine) as db:
        seed_a1(db)
        db.commit()
    env.set_user(1)
    verified = stats(env)
    monkeypatch.setattr(accountStatsServices, "SOURCE_TIMEZONE", None)
    body = stats(env)
    assert body["summary"] == verified["summary"] and body["regions"] == verified["regions"]
    cal = body["calendar"]
    assert cal["status"] == "unavailable" and cal["reason"] == "source_timezone_unverified" and cal["source_timezone"] is None
    assert (cal["current_streak_days"], cal["longest_streak_days"], cal["today_done"], cal["weekly_solved_problem_count"]) == (None, None, None, None)
    assert all(d["solved_problem_count"] is None for d in cal["recent7"]) and all(d["state"] == "unavailable" for d in cal["week_days"])
    assert len(cal["recent7"]) == 7 and len(cal["week_days"]) == 7
    assert body["quality"]["future_graded_submission_count"] is None and body["quality"]["ungraded_submission_count"] == 1


def test_zero_graded_records_is_an_honest_ready_zero_even_if_clock_unverified(env, monkeypatch):
    monkeypatch.setattr(accountStatsServices, "SOURCE_TIMEZONE", None)
    env.set_user(4)
    body = stats(env)
    cal = body["calendar"]
    assert cal["status"] == "ready" and cal["reason"] == "no_graded_records"
    assert (cal["current_streak_days"], cal["longest_streak_days"], cal["today_done"], cal["weekly_solved_problem_count"]) == (0, 0, False, 0)
    assert [d["solved_problem_count"] for d in cal["recent7"]] == [0] * 7
    assert [d["state"] for d in cal["week_days"]] == ["empty"] * 5 + ["future"] * 2
    assert body["summary"] == {"solved_problem_count": 0, "correct_count": 0, "partial_count": 0, "wrong_count": 0, "correct_rate_pct": None, "studied_parts_count": 0}
    assert all(r["correct_rate_pct"] is None and r["problem_count"] == 0 for r in body["regions"])
    # 미채점만 있어도 같다(미채점은 제외 건수로만)
    add(env, [_rv(50, 4, 101, None, _dt("2026-11-06 09:00")), _rv(51, 4, 102, "X", _dt("2026-11-06 09:00"))])
    again = stats(env)
    assert again["summary"]["solved_problem_count"] == 0 and again["quality"]["ungraded_submission_count"] == 2 and again["calendar"]["reason"] == "no_graded_records"


def test_as_of_changes_streak_text_basis(env, monkeypatch):
    with Session(env.engine) as db:
        seed_a1(db)
        db.commit()
    env.set_user(1)
    monkeypatch.setattr(accountStatsServices, "_now_kst", lambda: datetime.fromisoformat("2026-11-07T12:00:00+09:00"))
    cal = stats(env)["calendar"]
    assert (cal["today_done"], cal["current_streak_days"], cal["longest_streak_days"]) == (False, 7, 7)   # 오늘은 아직, 어제까지 이어짐
    monkeypatch.setattr(accountStatsServices, "_now_kst", lambda: datetime.fromisoformat("2026-11-08T12:00:00+09:00"))
    cal = stats(env)["calendar"]
    assert (cal["today_done"], cal["current_streak_days"], cal["longest_streak_days"]) == (False, 0, 7)    # 이틀 비면 0, 최장은 유지


def test_same_day_repeat_does_not_extend_streak_and_other_day_counts_each(env):
    add(env, [_rv(60, 1, 101, "C", _dt("2026-11-06 08:00")), _rv(61, 1, 101, "W", _dt("2026-11-06 09:00")), _rv(62, 1, 102, "C", _dt("2026-11-06 10:00")),
              _rv(63, 1, 101, "C", _dt("2026-11-04 10:00"))])
    env.set_user(1)
    cal = stats(env)["calendar"]
    assert [d["solved_problem_count"] for d in cal["recent7"]][-1] == 2 and [d["solved_problem_count"] for d in cal["recent7"]][-3] == 1
    assert cal["current_streak_days"] == 1 and cal["weekly_solved_problem_count"] == 2   # 같은 문제 주중 여러 번 → 주간 1(101)+1(102)
    assert stats(env)["summary"]["solved_problem_count"] == 2


def test_week_boundaries_and_utc_midnight(env, monkeypatch):
    """월요일 00:00 경계, UTC 15:00 = KST 다음날 00:00 경계(검증된 UTC 원시 시계)."""
    monkeypatch.setattr(accountStatsServices, "SOURCE_TIMEZONE", "UTC")
    add(env, [_rv(70, 1, 101, "C", _dt("2026-11-01 14:59:59")),      # KST 11/01 23:59:59 → 일요일(지난주)
              _rv(71, 1, 102, "C", _dt("2026-11-01 15:00:00")),      # KST 11/02 00:00:00 → 월요일(이번 주 첫날)
              _rv(72, 1, 103, "C", _dt("2026-11-06 02:59:59"))])     # KST 11/06 11:59:59 → as_of(12:00) 직전
    env.set_user(1)
    cal = stats(env)["calendar"]
    assert cal["weekly_solved_problem_count"] == 2
    assert [d["state"] for d in cal["week_days"]][:5] == ["learned", "empty", "empty", "empty", "learned"]
    assert [d["solved_problem_count"] for d in cal["recent7"]] == [0, 1, 1, 0, 0, 0, 1]


def test_future_and_undated_rows_count_in_totals_but_not_dates(env):
    add(env, [_rv(80, 1, 101, "C", _dt("2026-11-07 01:00")), _rv(81, 1, 102, "W", None), _rv(82, 1, 103, "H", _dt("2026-11-06 11:59:59"))])
    env.set_user(1)
    body = stats(env)
    assert body["summary"]["solved_problem_count"] == 3 and body["quality"]["future_graded_submission_count"] == 1 and body["quality"]["undated_graded_submission_count"] == 1
    assert [d["solved_problem_count"] for d in body["calendar"]["recent7"]][-1] == 1 and body["calendar"]["weekly_solved_problem_count"] == 1


def test_last_valid_grade_selection_and_ties(env):
    """문제별 마지막 유효 채점: 같은 시각은 idx 큰 것, 날짜 없는 행은 후순위, 미채점 행이 나중에 생겨도 과거 유효 채점 유지."""
    add(env, [_rv(90, 1, 101, "W", _dt("2026-11-05 10:00")), _rv(91, 1, 101, "C", _dt("2026-11-05 10:00")),          # 같은 시각 → idx 91(C)
              _rv(92, 1, 102, "C", None), _rv(93, 1, 102, "H", _dt("2026-11-01 10:00")),                              # 날짜 있는 H 가 우선
              _rv(94, 1, 103, "W", _dt("2026-11-01 10:00")), _rv(95, 1, 103, None, _dt("2026-11-05 10:00")),         # 나중 미채점이 W 를 지우지 않음
              _rv(96, 1, 104, "c ", _dt("2026-11-05 10:00"))])                                                        # trim+upper → C
    env.set_user(1)
    s = stats(env)["summary"]
    assert (s["solved_problem_count"], s["correct_count"], s["partial_count"], s["wrong_count"]) == (4, 2, 1, 1)


def test_rate_rounding_half_up_and_zero_denominator(env):
    rows = [_rv(100 + i, 1, 101 + (i % 1), "W", _dt("2026-11-01 10:00")) for i in range(1)]
    add(env, rows)
    env.set_user(1)
    s = stats(env)["summary"]
    assert s["correct_rate_pct"] == 0.0            # 기록이 있고 정답이 없으면 0%(null 아님)
    assert accountStatsServices._rate(1, 16) == 6.3 and accountStatsServices._rate(1, 3) == 33.3 and accountStatsServices._rate(2, 3) == 66.7
    assert accountStatsServices._rate(0, 0) is None


def test_zero_rate_regions_and_unclassified_split(env):
    add(env, [_rv(110, 1, 101, "H", _dt("2026-11-05 10:00")), _rv(111, 1, 105, "C", _dt("2026-11-05 10:00")), _rv(112, 1, 107, "C", _dt("2026-11-05 10:00")),
              _rv(113, 1, 106, "W", _dt("2026-11-05 10:00"))])
    env.set_user(1)
    body = stats(env)
    regions = {r["code"]: r for r in body["regions"]}
    assert regions["1"]["correct_rate_pct"] == 0.0 and regions["1"]["partial_count"] == 1 and regions["2"]["correct_rate_pct"] is None
    assert body["quality"]["unclassified_problem_count"] == 3 and body["summary"]["studied_parts_count"] == 1 and body["summary"]["solved_problem_count"] == 4


def test_other_member_data_does_not_leak(env):
    add(env, [_rv(120, 2, 101, "C", _dt("2026-11-06 09:00")), _rv(121, 2, 102, "C", _dt("2026-11-05 09:00"))])
    env.set_user(1)
    assert stats(env)["summary"]["solved_problem_count"] == 0
    env.set_user(2)
    assert stats(env)["summary"]["solved_problem_count"] == 2


# ── 형식·안전·읽기 전용 (ST11, ST12) ──────────────────────────────────────────
def test_response_has_no_sensitive_or_extra_fields_and_stable_shape(env):
    with Session(env.engine) as db:
        seed_a1(db)
        db.commit()
    env.set_user(1)
    res = env.get("/me/stats")
    raw = res.text
    for sentinel in ("SENTINEL", "1999-09-09", "a@example.test", "에이", "pw-sentinel", "가학교"):
        assert sentinel not in raw
    body = res.json()
    assert len(body["regions"]) == 4 and len(body["calendar"]["recent7"]) == 7 and len(body["calendar"]["week_days"]) == 7
    assert set(body["summary"]) == {"solved_problem_count", "correct_count", "partial_count", "wrong_count", "correct_rate_pct", "studied_parts_count"}
    assert set(body["quality"]) == {"ungraded_submission_count", "undated_graded_submission_count", "future_graded_submission_count", "unclassified_problem_count"}
    assert len(raw.encode("utf-8")) < 32 * 1024
    # 기존 /me 응답은 변하지 않는다
    me = env.get("/me").json()
    assert set(me) == {"ok", "profile", "recent_learning"}


def test_read_only_two_selects_no_writes_no_commit(env):
    with Session(env.engine) as db:
        seed_a1(db)
        db.commit()
    env.set_user(1)
    env.statements.clear()
    stats(env)
    kinds = [s.lstrip().split(None, 1)[0].upper() for s in env.statements]
    assert kinds == ["SELECT", "WITH"], env.statements   # 인증 조회 1 + 집계 1(CTE 한 문장)
    forbidden = ("INSERT", "UPDATE", "DELETE", "COMMIT", "CREATE", "ALTER", "DROP", "TRUNCATE")
    assert not [s for s in env.statements if s.lstrip().split(None, 1)[0].upper() in forbidden]
    with Session(env.engine) as db:   # 본인 제출·문제는 시험 전후 그대로
        assert db.execute(text("SELECT count(*) FROM review_node")).scalar() == len(A1) + 3


def test_sql_uses_bind_parameters_only(env):
    from repositories import accountStatsRepositories as repo

    sql = str(repo._STATS_SQL)
    for token in (":uid", ":shift", ":as_of", ":today", ":week_start", ":goal"):
        assert token in sql
    assert "%s" not in sql and "{" not in sql.replace("jsonb_build_object", "")   # f-string/포맷 조립 없음


def test_unexpected_error_is_fixed_500_without_leak(env, monkeypatch, caplog):
    env.set_user(1)

    def boom(*a, **k):
        raise RuntimeError("SENTINEL-DB-DETAIL postgresql://user:pw@host/db")

    monkeypatch.setattr(accountStatsServices.repositories, "aggregate_stats", boom)
    with caplog.at_level(logging.ERROR):
        res = env.get("/me/stats")
    assert res.status_code == 500 and res.headers["cache-control"] == "no-store"
    assert res.json() == {"ok": False, "detail": "서버 처리 중 문제가 생겼습니다."}
    assert "SENTINEL" not in res.text and "SENTINEL" not in caplog.text and "RuntimeError" in caplog.text


def test_invalid_aggregate_result_closes_as_fixed_error_not_silently_fixed(env, monkeypatch):
    env.set_user(1)
    real = accountStatsServices.repositories.aggregate_stats

    def bad(*a, **k):
        row = real(*a, **k)
        row["solved"] = row["solved"] + 5          # 합계가 어긋난 값
        return row

    monkeypatch.setattr(accountStatsServices.repositories, "aggregate_stats", bad)
    assert env.get("/me/stats").status_code == 500


# ── DTO 단위 (ST11) ──────────────────────────────────────────────────────────
def _calendar(**over):
    base = dict(status="ready", reason=None, source_timezone="UTC", today="2026-11-06", week_start="2026-11-02", week_end_exclusive="2026-11-09",
                daily_goal=1, today_solved_problem_count=0, current_streak_days=0, longest_streak_days=0, today_done=False, weekly_solved_problem_count=0,
                recent7=[{"date": f"2026-11-0{d}", "solved_problem_count": 0} for d in range(1, 8)],
                week_days=[{"date": f"2026-11-0{d}", "state": "empty"} for d in range(2, 9)])
    base.update(over)
    return base


def test_schema_rejects_bool_negative_out_of_range_and_extra():
    S.StatsCalendar(**_calendar())
    for bad in (_calendar(current_streak_days=True), _calendar(current_streak_days=-1), _calendar(unknown=1),
                _calendar(recent7=_calendar()["recent7"][:6]), _calendar(status="unavailable")):
        with pytest.raises(ValidationError):
            S.StatsCalendar(**bad)
    for bad in (dict(solved_problem_count=1, correct_count=1, partial_count=0, wrong_count=0, correct_rate_pct=100.5, studied_parts_count=1),
                dict(solved_problem_count=1, correct_count=1, partial_count=0, wrong_count=0, correct_rate_pct=float("nan"), studied_parts_count=1),
                dict(solved_problem_count=2, correct_count=1, partial_count=0, wrong_count=0, correct_rate_pct=50.0, studied_parts_count=1),
                dict(solved_problem_count=0, correct_count=0, partial_count=0, wrong_count=0, correct_rate_pct=0.0, studied_parts_count=0),
                dict(solved_problem_count=True, correct_count=1, partial_count=0, wrong_count=0, correct_rate_pct=100.0, studied_parts_count=1),
                dict(solved_problem_count=1, correct_count=1, partial_count=0, wrong_count=0, correct_rate_pct=100.0, studied_parts_count=5)):
        with pytest.raises(ValidationError):
            S.StatsSummary(**bad)
    with pytest.raises(ValidationError):   # 정답 수와 모순인 정답률은 보정하지 않고 거절
        S.StatsSummary(solved_problem_count=2, correct_count=1, partial_count=1, wrong_count=0, correct_rate_pct=100.0, studied_parts_count=1)
    with pytest.raises(ValidationError):
        S.RegionStat(code="1", label="뇌", problem_count=1, correct_count=1, partial_count=0, wrong_count=0, correct_rate_pct=0.0)
    S.StatsSummary(solved_problem_count=16, correct_count=1, partial_count=15, wrong_count=0, correct_rate_pct=6.3, studied_parts_count=1)   # 1/16=6.25→6.3 허용
    with pytest.raises(ValidationError):
        S.StatsResponse(owner_idx=0, as_of="x", summary=None, calendar=None, regions=[], quality=None)


def test_source_timezone_policy_is_closed_set(env):
    env.set_user(1)
    with pytest.raises(ValueError):
        with Session(env.engine) as db:
            accountStatsServices.get_stats(db, {"learner": {"idx": 1, "email_verified": True}}, source_timezone="Asia/Tokyo")


# ── 하루 목표(실제 기본값 5문제): 그날 서로 다른 문제를 5개 이상 풀어야 연속 학습일 ─────────────────────────
def _day(user, base_idx, day, count, first_study=101, code="C"):
    """day(예: '2026-11-05') 에 서로 다른 문제 count 개를 푼 채점 제출. 문제 번호는 first_study 부터 연속(101~107, 없으면 새 번호)."""
    return [_rv(base_idx + i, user, first_study + i, code, _dt(f"{day} 10:{i:02d}")) for i in range(count)]


@pytest.fixture
def goal5(env, monkeypatch):
    monkeypatch.setattr(accountStatsServices, "DAILY_GOAL", 5)
    return env


def test_default_daily_goal_is_five():
    import importlib

    fresh = importlib.reload(accountStatsServices)
    assert fresh.DAILY_GOAL == 5


def test_default_source_timezone_is_seoul_by_user_decision():
    """사용자 결정(2026-11-06): 저장 시계는 한국 시간. 기본값이 'Asia/Seoul' 이고, 미확인(None) 폴백은 시험이 주입할 때만 쓰인다."""
    assert accountStatsServices.SOURCE_TIMEZONE == "Asia/Seoul"
    assert accountStatsServices.ALLOWED_SOURCE_TIMEZONES == (None, "UTC", "Asia/Seoul")


def test_goal5_four_problems_do_not_count_five_do(goal5):
    add(goal5, _day(1, 200, "2026-11-04", 4) + _day(1, 210, "2026-11-05", 5) + _day(1, 220, "2026-11-06", 4))
    goal5.set_user(1)
    cal = stats(goal5)["calendar"]
    assert cal["daily_goal"] == 5 and cal["today_solved_problem_count"] == 4 and cal["today_done"] is False
    assert (cal["current_streak_days"], cal["longest_streak_days"]) == (1, 1)      # 11/05(5개)만 학습일, 11/04·오늘(4개)은 미달
    states = [d["state"] for d in cal["week_days"]]
    assert states == ["empty", "empty", "partial", "learned", "partial", "future", "future"]
    assert [d["solved_problem_count"] for d in cal["recent7"]][-3:] == [4, 5, 4]   # 막대는 실제로 푼 수 그대로


def test_goal5_exactly_five_today_is_done_and_extends_streak(goal5):
    add(goal5, _day(1, 300, "2026-11-04", 5) + _day(1, 310, "2026-11-05", 5) + _day(1, 320, "2026-11-06", 5))
    goal5.set_user(1)
    cal = stats(goal5)["calendar"]
    assert (cal["today_solved_problem_count"], cal["today_done"], cal["current_streak_days"], cal["longest_streak_days"]) == (5, True, 3, 3)


def test_goal5_same_problem_repeated_counts_once(goal5):
    add(goal5, [_rv(400 + i, 1, 101, "C", _dt(f"2026-11-06 10:{i:02d}")) for i in range(9)])        # 같은 문제를 9번
    goal5.set_user(1)
    cal = stats(goal5)["calendar"]
    assert cal["today_solved_problem_count"] == 1 and cal["today_done"] is False and cal["current_streak_days"] == 0


def test_goal5_streak_continues_until_midnight_when_today_not_yet_met(goal5):
    add(goal5, _day(1, 500, "2026-11-04", 5) + _day(1, 510, "2026-11-05", 6) + _day(1, 520, "2026-11-06", 2))
    goal5.set_user(1)
    cal = stats(goal5)["calendar"]
    assert (cal["today_solved_problem_count"], cal["today_done"], cal["current_streak_days"]) == (2, False, 2)   # 어제까지 2일 연속이 오늘 자정까지 유지


def test_goal5_partial_day_breaks_longest_and_current_streak(goal5, monkeypatch):
    add(goal5, _day(1, 600, "2026-11-01", 5) + _day(1, 610, "2026-11-02", 5) + _day(1, 620, "2026-11-03", 4) + _day(1, 630, "2026-11-04", 5) + _day(1, 640, "2026-11-05", 5))
    goal5.set_user(1)
    cal = stats(goal5)["calendar"]
    assert (cal["longest_streak_days"], cal["current_streak_days"], cal["today_done"], cal["today_solved_problem_count"]) == (2, 2, False, 0)   # 11/03(4개)이 끊음
    monkeypatch.setattr(accountStatsServices, "_now_kst", lambda: datetime.fromisoformat("2026-11-07T12:00:00+09:00"))
    assert stats(goal5)["calendar"]["current_streak_days"] == 0                                                                                # 하루 비면 0(최장 2 유지)
    assert stats(goal5)["calendar"]["longest_streak_days"] == 2


def test_goal5_week_day_states_and_zero_records_and_unverified(goal5, monkeypatch):
    goal5.set_user(4)
    cal = stats(goal5)["calendar"]
    assert (cal["daily_goal"], cal["today_solved_problem_count"], cal["today_done"]) == (5, 0, False) and cal["reason"] == "no_graded_records"
    assert [d["state"] for d in cal["week_days"]] == ["empty"] * 5 + ["future"] * 2
    add(goal5, _day(1, 700, "2026-11-05", 3))
    monkeypatch.setattr(accountStatsServices, "SOURCE_TIMEZONE", None)
    goal5.set_user(1)
    cal = stats(goal5)["calendar"]
    assert cal["status"] == "unavailable" and cal["today_solved_problem_count"] is None and cal["daily_goal"] == 5
    assert all(d["state"] == "unavailable" for d in cal["week_days"])


def test_goal5_progress_midday_and_midnight_boundary_use_server_clock(goal5, monkeypatch):
    add(goal5, _day(1, 800, "2026-11-05", 3))
    goal5.set_user(1)
    monkeypatch.setattr(accountStatsServices, "_now_kst", lambda: datetime.fromisoformat("2026-11-05T23:59:59+09:00"))
    assert stats(goal5)["calendar"]["today_solved_problem_count"] == 3
    monkeypatch.setattr(accountStatsServices, "_now_kst", lambda: datetime.fromisoformat("2026-11-06T00:00:00+09:00"))
    cal = stats(goal5)["calendar"]
    assert cal["today_solved_problem_count"] == 0 and cal["recent7"][-2]["solved_problem_count"] == 3


def test_daily_goal_input_is_closed_and_not_from_request(goal5):
    goal5.set_user(1)
    base = stats(goal5)
    assert goal5.get("/me/stats?goal=1&daily_goal=1").json() == base          # 요청으로 목표를 바꿀 수 없다
    for bad in (0, -1, True, "5", 2.5):
        with pytest.raises(ValueError):
            with Session(goal5.engine) as db:
                accountStatsServices.get_stats(db, {"learner": {"idx": 1, "email_verified": True}}, daily_goal=bad)


def test_schema_today_done_must_follow_daily_goal():
    S.StatsCalendar(**_calendar(daily_goal=5, today_solved_problem_count=5, today_done=True))
    for bad in (_calendar(daily_goal=5, today_solved_problem_count=4, today_done=True), _calendar(daily_goal=5, today_solved_problem_count=5, today_done=False),
                _calendar(daily_goal=0), _calendar(today_solved_problem_count=None), _calendar(daily_goal=True)):
        with pytest.raises(ValidationError):
            S.StatsCalendar(**bad)
    wd = _calendar()["week_days"]
    wd[0]["state"] = "partial"
    S.StatsCalendar(**_calendar(week_days=wd))
