"""학습자 학습 현황 API 스키마."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TodayPartStat(BaseModel):
    st_part: str = Field(..., description="1 뇌, 2 흉부, 3 복부, 4 무릎")
    part_label: str
    study_minutes: int = Field(..., description="오늘 그 부위 첫 제출~마지막 제출 분")
    problem_count: int = Field(..., description="오늘 그 부위 제출 수")
    correct_rate: int = Field(..., description="정답(C) 비율. 0~100 정수")


class TodayLearningOut(BaseModel):
    ok: bool = True
    date: str
    user_idx: int
    items: list[TodayPartStat]
