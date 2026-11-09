"""GET /me/stats 집계 SQL 의 합성 성능 확인(ST13). ACCOUNT-FIND-MYPAGE-20261105 / FOLLOWUP-20261106.

임시 내장 PostgreSQL 한 개에 합성 제출 10만 건(본인 1만 건 + 다른 회원 9만 건)을 넣고 한 요청(2 SELECT)의 응답 시간을 10회 재서 중앙값/p95 를 출력한다.
목표: 중앙값 1초·p95 2초 이내, 응답 32KiB 이하. 이 수치는 **이 합성 임시 DB 한정**이며 실DB 성능을 보장하지 않는다.
미달하면 은폐하지 않고 보고한다(기간 절단·0 처리·DDL 로 맞추지 않는다 — 새 인덱스도 만들지 않는다).
요약은 `pytest -s` 로 확인한다. EXPLAIN(ANALYZE, BUFFERS) 결과도 출력한다.
"""
import shutil
import statistics
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db import Base
from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from models.userModel import UserModel
from repositories import accountStatsRepositories as repo
from services import accountStatsServices

KST_NOW = datetime.fromisoformat("2026-11-06T12:00:00+09:00")


@pytest.fixture(scope="module")
def big():
    import pgserver

    tmp = Path(tempfile.mkdtemp(prefix="account_stats_perf_pg_"))
    server = pgserver.get_server(tmp / "pg", cleanup_mode="stop")
    engine = create_engine(server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1))
    Base.metadata.create_all(engine, tables=[UserModel.__table__, StudyModel.__table__, ReviewNodeModel.__table__])
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO user_member (idx, user_id, user_name, user_birth, user_pw, user_un, user_sp, del_yn, state, mb_level) "
                          "SELECT g, 'u' || g || '@example.test', 'n' || g, '2000-01-01', 'x', 's', 'm', 'N', 'N', 1 FROM generate_series(1, 50) g"))
        conn.execute(text("INSERT INTO study (idx, st_part, st_modal, st_image, st_disease, del_yn, state) "
                          "SELECT g, CAST(1 + g % 4 AS text), '2', '/s.png', 'd', CASE WHEN g % 97 = 0 THEN 'Y' ELSE 'N' END, 'N' FROM generate_series(1, 2000) g"))
        # 본인(1) 1만 건 + 다른 회원 9만 건, 문제 2000개에 분산, 최근 200일, C/H/W + 일부 미채점
        conn.execute(text("""
            INSERT INTO review_node (rn_u_idx, rn_s_idx, rn_solution, created_at, del_yn, state)
            SELECT CASE WHEN g <= 10000 THEN 1 ELSE 2 + (g % 49) END,
                   1 + (g * 7919) % 2000,
                   (ARRAY['C','H','W','C','X'])[1 + g % 5],
                   TIMESTAMP '2026-11-06 11:00:00' - (g % 200) * interval '1 day' - (g % 1440) * interval '1 minute',
                   'N', 'N'
            FROM generate_series(1, 100000) g"""))
        conn.execute(text("ANALYZE"))
    yield engine
    engine.dispose()
    server.cleanup()
    shutil.rmtree(tmp, ignore_errors=True)


def _one_request(engine):
    with Session(engine) as db:
        accountStatsServices.SOURCE_TIMEZONE = "Asia/Seoul"
        return accountStatsServices.get_stats(db, {"learner": {"idx": 1, "email_verified": True}}, now=KST_NOW)


def test_stats_aggregate_is_fast_enough_on_100k_rows(big, capsys):
    saved = accountStatsServices.SOURCE_TIMEZONE
    try:
        first = _one_request(big)    # 워밍업(계획 캐시 등)
        assert first.summary.solved_problem_count > 0 and first.calendar.longest_streak_days > 0
        times = []
        for _ in range(10):
            t0 = time.perf_counter()
            result = _one_request(big)
            times.append(time.perf_counter() - t0)
        size = len(result.model_dump_json().encode("utf-8"))
        median, p95 = statistics.median(times), sorted(times)[-1]   # 10회라 p95 는 최댓값으로 보수적으로 잡는다
        with Session(big) as db:   # 같은 SQL 의 실행 계획(바인드 값은 고정 리터럴로 치환해 EXPLAIN 한다)
            raw_sql = str(repo._STATS_SQL).replace(":uid", "1").replace(":shift", "0").replace(":goal", "5").replace(":as_of", "TIMESTAMP '2026-11-06 12:00:00'")                 .replace(":today", "'2026-11-06'").replace(":recent_start", "'2026-10-31'").replace(":week_start", "'2026-11-02'").replace(":week_end", "'2026-11-09'")
            plan = [r[0] for r in db.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + raw_sql)).all()]
        with capsys.disabled():
            print(f"\n[ST13] 합성 10만행(본인1만) 응답 {len(times)}회: median={median*1000:.0f}ms p95={p95*1000:.0f}ms max={max(times)*1000:.0f}ms 크기={size}B 문제={first.summary.solved_problem_count}")
            print("[ST13] EXPLAIN 마지막 6줄:\n  " + "\n  ".join(plan[-6:]))
        assert size < 32 * 1024
        assert median < 1.0 and p95 < 2.0
    finally:
        accountStatsServices.SOURCE_TIMEZONE = saved
