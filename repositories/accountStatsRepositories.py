"""마이페이지 학습 통계 전용 DB 읽기. 읽기만 한다 — insert/update/delete/commit 을 하지 않는다.

모든 지표는 **한 번의 SELECT(CTE)** 로 계산한다(요청 하나의 숫자가 서로 다른 시점에서 섞이지 않도록). 본인 제출을 파이썬으로 가져와 세지 않고,
문제마다 SQL 을 보내지 않는다. 회원 번호·날짜·시간대는 모두 바인드 파라미터이며, 사용자 입력은 SQL 문자열에 들어가지 않는다.
이 SQL 은 PostgreSQL 전용이다(jsonb 집계·FILTER). 새 DB 칸·인덱스는 만들지 않는다.
시계 변환은 DB 시간대 이름(AT TIME ZONE 'Asia/Seoul')이 아니라 **고정 시간 이동(:shift)** 으로 한다: 한국은 일광절약시간이 없어 KST=UTC+9 이고,
저장 시계가 KST 로 확인되면 shift=0(추가 +9 없음), UTC 로 확인되면 shift=9, 확인 전이면 NULL(날짜 계산 안 함). DB 의 시간대 데이터에 기대지 않는다.

지표 정의(FOLLOWUP_DESIGN.md 2-1):
- 채점 제출: 본인·삭제 안 된 제출·rn_s_idx > 0 중 upper(btrim(rn_solution)) 가 C/H/W 인 행. 그 밖의 값/NULL 은 오답으로 바꾸지 않고 '미채점 제외'로 센다.
- 푼 문제 수/정답률/부위: 문제(rn_s_idx)별 **마지막 유효 채점** 한 건(created_at DESC NULLS LAST, idx DESC).
- 이번 주/최근 7일/연속일: 모든 유효 채점 제출에서 날짜별 DISTINCT 문제. **연속 학습일은 하루 목표(:goal) 문제 이상 푼 날만 학습일로 센다.** 날짜는 검증된 source 시계(:shift)로만 KST 로 바꾼다(NULL 이면 날짜 계산 안 함).
"""
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

_STATS_SQL = text(
    """
WITH owned AS (
  SELECT r.idx,
         r.rn_s_idx AS sid,
         upper(btrim(r.rn_solution)) AS code,
         r.created_at,
         r.created_at + CAST(:shift AS integer) * interval '1 hour' AS kst
  FROM review_node r
  WHERE r.rn_u_idx = :uid AND r.del_yn = 'N' AND r.rn_s_idx > 0
),
graded AS (
  SELECT * FROM owned WHERE code IN ('C', 'H', 'W')
),
ranked AS (
  SELECT g.sid, g.code,
         row_number() OVER (PARTITION BY g.sid ORDER BY g.created_at DESC NULLS LAST, g.idx DESC) AS rn
  FROM graded g
),
problems AS (
  SELECT sid, code FROM ranked WHERE rn = 1
),
classified AS (
  SELECT p.code,
         CASE WHEN s.idx IS NOT NULL AND s.del_yn = 'N' AND s.st_part IN ('1', '2', '3', '4') THEN s.st_part END AS part
  FROM problems p
  LEFT JOIN study s ON s.idx = p.sid
),
clocked AS (
  SELECT sid, CAST(kst AS date) AS d
  FROM graded
  WHERE CAST(:shift AS integer) IS NOT NULL AND created_at IS NOT NULL AND kst <= :as_of
),
daily AS (
  SELECT d, count(DISTINCT sid) AS n FROM clocked GROUP BY d
),
qualified AS (
  SELECT d FROM daily WHERE n >= CAST(:goal AS integer)
),
islands AS (
  SELECT d, d - CAST(row_number() OVER (ORDER BY d) AS integer) AS grp FROM qualified
),
runs AS (
  SELECT min(d) AS s, max(d) AS e, count(*) AS len FROM islands GROUP BY grp
),
spine AS (
  SELECT CAST(g AS date) AS d FROM generate_series(CAST(:recent_start AS date), CAST(:today AS date), interval '1 day') AS g
)
SELECT
  (SELECT count(*) FROM problems) AS solved,
  (SELECT count(*) FROM problems WHERE code = 'C') AS c,
  (SELECT count(*) FROM problems WHERE code = 'H') AS h,
  (SELECT count(*) FROM problems WHERE code = 'W') AS w,
  (SELECT coalesce(jsonb_agg(jsonb_build_object(
            'code', k.part, 'problem_count', coalesce(x.total, 0), 'correct_count', coalesce(x.c, 0),
            'partial_count', coalesce(x.h, 0), 'wrong_count', coalesce(x.w, 0)) ORDER BY k.part), CAST('[]' AS jsonb))
     FROM (VALUES ('1'), ('2'), ('3'), ('4')) AS k(part)
     LEFT JOIN (SELECT part, count(*) AS total,
                       count(*) FILTER (WHERE code = 'C') AS c,
                       count(*) FILTER (WHERE code = 'H') AS h,
                       count(*) FILTER (WHERE code = 'W') AS w
                FROM classified WHERE part IS NOT NULL GROUP BY part) x ON x.part = k.part) AS regions,
  (SELECT count(*) FROM classified WHERE part IS NULL) AS unclassified,
  (SELECT count(*) FROM owned WHERE code IS NULL OR code NOT IN ('C', 'H', 'W')) AS ungraded,
  (SELECT count(*) FROM graded WHERE created_at IS NULL) AS undated,
  (SELECT count(*) FROM graded WHERE CAST(:shift AS integer) IS NOT NULL AND created_at IS NOT NULL AND kst > :as_of) AS future_graded,
  (SELECT count(DISTINCT sid) FROM clocked WHERE d >= CAST(:week_start AS date) AND d < CAST(:week_end AS date)) AS weekly,
  (SELECT coalesce(jsonb_agg(jsonb_build_object('date', to_char(dl.d, 'YYYY-MM-DD'), 'n', dl.n) ORDER BY dl.d), CAST('[]' AS jsonb))
     FROM daily dl WHERE dl.d >= CAST(:week_start AS date) AND dl.d < CAST(:week_end AS date)) AS week_counts,
  (SELECT coalesce(jsonb_agg(jsonb_build_object('date', to_char(sp.d, 'YYYY-MM-DD'), 'n', coalesce(dl.n, 0)) ORDER BY sp.d), CAST('[]' AS jsonb))
     FROM spine sp LEFT JOIN daily dl ON dl.d = sp.d) AS recent7,
  (SELECT coalesce(max(len), 0) FROM runs) AS longest,
  (SELECT max(len) FROM runs WHERE e = CAST(:today AS date)) AS run_end_today,
  (SELECT max(len) FROM runs WHERE e = CAST(:today AS date) - 1) AS run_end_yesterday
"""
)


def aggregate_stats(db: Session, *, user_idx: int, as_of_kst: datetime, today: date, source_timezone: str | None, daily_goal: int) -> dict:
    """본인의 통계 원자료를 한 번의 SELECT 로 읽는다. as_of_kst 는 시간대 없는 KST 달력 시각, source_timezone 은 검증된 저장 시계('UTC'/'Asia/Seoul'/None)."""
    from datetime import timedelta

    week_start = today - timedelta(days=today.weekday())
    params = {
        "uid": user_idx,
        "shift": {None: None, "Asia/Seoul": 0, "UTC": 9}[source_timezone],
        "as_of": as_of_kst,
        "today": today,
        "goal": daily_goal,
        "recent_start": today - timedelta(days=6),
        "week_start": week_start,
        "week_end": week_start + timedelta(days=7),
    }
    row = db.execute(_STATS_SQL, params).mappings().one()
    return dict(row)
