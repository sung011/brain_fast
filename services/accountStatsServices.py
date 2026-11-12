"""마이페이지 학습 통계(시안 B). 읽기만 한다 — DB 에 쓰지 않고 영상·저장소·모델·외부 서비스를 부르지 않는다.

회원은 요청이 아니라 **서버 세션의 learner** 로만 정한다(GET /me 와 같은 인증 규칙). 날짜 기준은 서버가 정한 Asia/Seoul 달력과
서버가 한 번 계산한 as_of 뿐이다 — 브라우저 시간·시간대·날짜를 받지 않는다.

저장 시각(created_at)은 시간대 없는 값이라 **원시 시계를 UTC 라고도 KST 라고도 가정하지 않는다.**
저장 시계(SOURCE_TIMEZONE)는 사용자 결정으로 한국 시간(Asia/Seoul)이다. 시계가 None(미확인)으로 설정되면 날짜 지표(연속일·이번 주·최근 7일·요일)를 null 로 두고(calendar.status=unavailable),
총 문제 수·정답률·부위별 값은 실제 기록으로 보여 준다. 채점 기록이 0건이면 해석할 시각이 없으므로 0 으로 정직하게 보여 준다.
SOURCE_TIMEZONE 은 코드/시험에서만 정한다(.env·세션 시간대 SET·데이터 보정 UPDATE 를 하지 않는다).
설계: designs/ACCOUNT-FIND-MYPAGE-20261105/FOLLOWUP_DESIGN.md.
"""
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from repositories import accountProfileRepositories as profile_repositories
from repositories import accountStatsRepositories as repositories
from schemas.accountStatsSchemas import (
    DayCount,
    RegionStat,
    StatsCalendar,
    StatsQuality,
    StatsResponse,
    StatsSummary,
    WeekDay,
)
from schemas.studySchemas import ST_PART_LABELS
from services.accountProfileServices import NotApproved, NotAuthenticated, _learner_idx  # noqa: F401  (예외 형식·인증 규칙을 /me 와 공유)

KST = timezone(timedelta(hours=9), "Asia/Seoul")   # 한국은 일광절약시간이 없어 고정 +09:00 으로 충분하다
ALLOWED_SOURCE_TIMEZONES = (None, "UTC", "Asia/Seoul")

# 저장 시각(created_at)의 원시 시계. **사용자 결정(2026-11-06): 한국 시간(Asia/Seoul)으로 확정**한다 — 저장된 시각을 그대로 한국 시각으로 읽는다(+9시간 보정 없음).
# 이 결정은 사용자의 제품 결정이며, 실DB 저장 시계를 읽기 전용으로 확인한 결과가 아니다(실DB 조회 0). 실제 값이 UTC 로 밝혀지면 'UTC' 로 바꾼다.
# None 은 '시계 미확인' 폴백(날짜 지표 null)으로 계속 지원하며 시험이 주입한다.
SOURCE_TIMEZONE: str | None = "Asia/Seoul"

# 연속 학습일로 인정하는 하루 목표: 그날 서로 다른 문제를 이 개수 이상 풀어야 한다(사용자 결정 2026-11-06: 5문제). 시험은 값을 주입한다.
DAILY_GOAL = 5


def _now_kst() -> datetime:
    return datetime.now(KST)


def _rate(correct: int, total: int) -> float | None:
    if total <= 0:
        return None
    return float((Decimal(correct) * 100 / Decimal(total)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _week_days(today, week_start, counts: dict[str, int], goal: int, state_mode: str) -> list[WeekDay]:
    days = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        if state_mode == "unavailable":
            state = "unavailable"
        elif day > today:
            state = "future"
        elif counts.get(day.isoformat(), 0) >= goal:
            state = "learned"
        elif counts.get(day.isoformat(), 0) > 0:
            state = "partial"
        else:
            state = "empty"
        days.append(WeekDay(date=day, state=state))
    return days


def get_stats(db: Session, session: object, *, source_timezone: str | None = None, now: datetime | None = None, daily_goal: int | None = None) -> StatsResponse:
    """GET /me/stats 본문. source_timezone/now/daily_goal 은 시험이 주입한다(라우터는 넘기지 않는다 → 모듈 설정과 현재 시각)."""
    idx, approved = _learner_idx(session)
    user = profile_repositories.get_active_member(db, idx)
    if user is None:
        raise NotAuthenticated
    if not approved:
        raise NotApproved
    source = SOURCE_TIMEZONE if source_timezone is None else source_timezone
    if source not in ALLOWED_SOURCE_TIMEZONES:
        raise ValueError("unsupported source timezone")
    goal = DAILY_GOAL if daily_goal is None else daily_goal
    if isinstance(goal, bool) or not isinstance(goal, int) or goal < 1:
        raise ValueError("daily goal must be a positive integer")
    as_of = (now or _now_kst()).astimezone(KST)
    today = as_of.date()
    raw = repositories.aggregate_stats(
        db, user_idx=user.idx, as_of_kst=as_of.replace(tzinfo=None), today=today, source_timezone=source, daily_goal=goal,
    )
    solved, c, h, w = int(raw["solved"]), int(raw["c"]), int(raw["h"]), int(raw["w"])
    regions_raw = {str(r["code"]): r for r in (raw["regions"] or [])}
    regions = []
    for code in ("1", "2", "3", "4"):
        r = regions_raw.get(code, {})
        total, rc, rh, rw = (int(r.get("problem_count", 0)), int(r.get("correct_count", 0)), int(r.get("partial_count", 0)), int(r.get("wrong_count", 0)))
        regions.append(RegionStat(code=code, label=ST_PART_LABELS[code], problem_count=total, correct_count=rc, partial_count=rh, wrong_count=rw,
                                  correct_rate_pct=_rate(rc, total)))
    summary = StatsSummary(
        solved_problem_count=solved, correct_count=c, partial_count=h, wrong_count=w, correct_rate_pct=_rate(c, solved),
        studied_parts_count=sum(1 for r in regions if r.problem_count > 0),
    )

    week_start = today - timedelta(days=today.weekday())
    recent_dates = [today - timedelta(days=6 - i) for i in range(7)]
    if solved == 0:
        # 해석할 채점 기록이 없다 → 시계가 미확인이어도 달력 숫자는 정직하게 0
        calendar = StatsCalendar(
            status="ready", reason="no_graded_records", source_timezone=source, today=today, week_start=week_start,
            week_end_exclusive=week_start + timedelta(days=7), daily_goal=goal, today_solved_problem_count=0, current_streak_days=0,
            longest_streak_days=0, today_done=False,
            weekly_solved_problem_count=0, recent7=[DayCount(date=d, solved_problem_count=0) for d in recent_dates],
            week_days=_week_days(today, week_start, {}, goal, "ready"),
        )
        future = 0 if source else None
    elif source is None:
        calendar = StatsCalendar(
            status="unavailable", reason="source_timezone_unverified", source_timezone=None, today=today, week_start=week_start,
            week_end_exclusive=week_start + timedelta(days=7), daily_goal=goal, today_solved_problem_count=None, current_streak_days=None,
            longest_streak_days=None, today_done=None,
            weekly_solved_problem_count=None, recent7=[DayCount(date=d, solved_problem_count=None) for d in recent_dates],
            week_days=_week_days(today, week_start, {}, goal, "unavailable"),
        )
        future = None
    else:
        by_date = {str(item["date"]): int(item["n"]) for item in (raw["recent7"] or [])}
        today_count = by_date.get(today.isoformat(), 0)
        today_done = today_count >= goal   # 오늘 서로 다른 문제를 하루 목표 이상 풀었는가
        end_today, end_yesterday = raw["run_end_today"], raw["run_end_yesterday"]
        # 목표를 채운 날(하루 목표 이상)만 학습일이다. 오늘 채웠으면 오늘 끝 연속 길이, 오늘은 아직이지만 어제까지 이었다면 그 길이를 오늘 자정까지 유지, 아니면 0
        current = int(end_today) if end_today else (int(end_yesterday) if end_yesterday else 0)
        calendar = StatsCalendar(
            status="ready", reason=None, source_timezone=source, today=today, week_start=week_start,
            week_end_exclusive=week_start + timedelta(days=7), daily_goal=goal, today_solved_problem_count=today_count,
            current_streak_days=current, longest_streak_days=int(raw["longest"]),
            today_done=today_done, weekly_solved_problem_count=int(raw["weekly"]),
            recent7=[DayCount(date=d, solved_problem_count=by_date.get(d.isoformat(), 0)) for d in recent_dates],
            week_days=_week_days(today, week_start, {str(x["date"]): int(x["n"]) for x in (raw["week_counts"] or [])}, goal, "ready"),
        )
        future = int(raw["future_graded"])

    quality = StatsQuality(
        ungraded_submission_count=int(raw["ungraded"]), undated_graded_submission_count=int(raw["undated"]),
        future_graded_submission_count=future, unclassified_problem_count=int(raw["unclassified"]),
    )
    return StatsResponse(owner_idx=user.idx, as_of=as_of.isoformat(timespec="seconds"), summary=summary, calendar=calendar, regions=regions, quality=quality)
