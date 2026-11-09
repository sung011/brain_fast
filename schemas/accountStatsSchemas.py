"""마이페이지 학습 통계(GET /me/stats) 응답 형식. 허용 필드만 명시한다(모든 중첩 모델 extra="forbid").

원시 제출 목록·영상 주소·병명·기준 박스·소견·다른 개인정보는 어떤 필드에도 두지 않는다. 숫자 필드는 bool 을 거절(StrictInt)하고
범위·합계가 어긋나면 값을 보정하지 않고 검증 오류로 닫는다(라우터가 고정 500 으로 답한다).
설계: designs/ACCOUNT-FIND-MYPAGE-20261105/FOLLOWUP_DESIGN.md 2~3절.
"""
import datetime as dt
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

Count = StrictInt
NonNegative = Field(ge=0)


def _rate_matches(rate: float | None, correct: int, total: int) -> bool:
    """정답률은 정답 수/전체 문제 수(소수 첫째 자리 반올림)와 일치해야 한다. 서로 모순인 값은 보정하지 않고 거절한다."""
    if total == 0:
        return rate is None
    return rate is not None and abs(rate - correct * 100 / total) <= 0.05 + 1e-9


def _rate_ok(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("rate out of range")
    return float(value)


class StatsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    solved_problem_count: Count = NonNegative
    correct_count: Count = NonNegative
    partial_count: Count = NonNegative
    wrong_count: Count = NonNegative
    correct_rate_pct: float | None = None
    studied_parts_count: Count = Field(ge=0, le=4)

    _rate = field_validator("correct_rate_pct")(_rate_ok)

    @model_validator(mode="after")
    def _sums(self):
        if self.correct_count + self.partial_count + self.wrong_count != self.solved_problem_count:
            raise ValueError("summary sum mismatch")
        if not _rate_matches(self.correct_rate_pct, self.correct_count, self.solved_problem_count):
            raise ValueError("rate mismatch")
        return self


class DayCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date
    solved_problem_count: StrictInt | None = Field(default=None, ge=0)


class WeekDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date
    state: Literal["learned", "partial", "empty", "future", "unavailable"]   # learned=하루 목표 달성, partial=풀었지만 목표 미달


class StatsCalendar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "unavailable"]
    reason: Literal["no_graded_records", "source_timezone_unverified"] | None = None
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    source_timezone: Literal["UTC", "Asia/Seoul"] | None = None
    today: dt.date
    week_start: dt.date
    week_end_exclusive: dt.date
    current_streak_days: StrictInt | None = Field(default=None, ge=0)
    longest_streak_days: StrictInt | None = Field(default=None, ge=0)
    daily_goal: StrictInt = Field(gt=0)
    today_solved_problem_count: StrictInt | None = Field(default=None, ge=0)
    today_done: StrictBool | None = None   # 오늘 푼 서로 다른 문제 수 >= daily_goal
    weekly_solved_problem_count: StrictInt | None = Field(default=None, ge=0)
    recent7: list[DayCount] = Field(min_length=7, max_length=7)
    week_days: list[WeekDay] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def _consistent(self):
        unavailable = self.status == "unavailable"
        numbers = [self.current_streak_days, self.longest_streak_days, self.today_done, self.weekly_solved_problem_count, self.today_solved_problem_count]
        if unavailable:
            if self.reason != "source_timezone_unverified" or any(v is not None for v in numbers) or any(d.solved_problem_count is not None for d in self.recent7):
                raise ValueError("unavailable calendar must not carry numbers")
            if any(d.state != "unavailable" for d in self.week_days):
                raise ValueError("unavailable calendar week_days")
        else:
            if self.reason not in (None, "no_graded_records") or any(v is None for v in numbers) or any(d.solved_problem_count is None for d in self.recent7):
                raise ValueError("ready calendar must carry numbers")
            if any(d.state == "unavailable" for d in self.week_days):
                raise ValueError("ready calendar week_days")
            if self.today_done != (self.today_solved_problem_count >= self.daily_goal):
                raise ValueError("today_done must follow the daily goal")
        return self


class RegionStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["1", "2", "3", "4"]
    label: Literal["뇌", "흉부", "복부", "무릎"]
    problem_count: Count = NonNegative
    correct_count: Count = NonNegative
    partial_count: Count = NonNegative
    wrong_count: Count = NonNegative
    correct_rate_pct: float | None = None

    _rate = field_validator("correct_rate_pct")(_rate_ok)

    @model_validator(mode="after")
    def _sums(self):
        if self.correct_count + self.partial_count + self.wrong_count != self.problem_count:
            raise ValueError("region sum mismatch")
        if not _rate_matches(self.correct_rate_pct, self.correct_count, self.problem_count):
            raise ValueError("region rate mismatch")
        return self


class StatsQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ungraded_submission_count: Count = NonNegative
    undated_graded_submission_count: Count = NonNegative
    future_graded_submission_count: StrictInt | None = Field(default=None, ge=0)
    unclassified_problem_count: Count = NonNegative


class StatsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    stats_version: Literal["motivation_v1"] = "motivation_v1"
    owner_idx: StrictInt = Field(gt=0)
    as_of: str
    summary: StatsSummary
    calendar: StatsCalendar
    regions: list[RegionStat] = Field(min_length=4, max_length=4)
    quality: StatsQuality

    @model_validator(mode="after")
    def _regions(self):
        if [r.code for r in self.regions] != ["1", "2", "3", "4"]:
            raise ValueError("region order")
        classified = sum(r.problem_count for r in self.regions)
        if classified + self.quality.unclassified_problem_count != self.summary.solved_problem_count:
            raise ValueError("region total mismatch")
        if self.summary.studied_parts_count != sum(1 for r in self.regions if r.problem_count > 0):
            raise ValueError("studied parts mismatch")
        return self
