"""부위 자동 판별 응답 형식."""
from pydantic import BaseModel, Field


class BodyPartResponse(BaseModel):
    label: str = Field(description="판별 라벨. brain_ct / brain_mri / thorax_xray / knee_mri / unknown")
    label_ko: str = Field(description="라벨의 한국어 이름")
    confidence: float = Field(description="판별 확신도 0~1. 모델이 없으면 0")
    model_ready: bool = Field(description="판별 모델이 준비됐는지. 아니면 늘 unknown")
    selected: str | None = Field(default=None, description="화면에서 고른 부위 값(brain, thorax 등)")
    expected: str | None = Field(default=None, description="고른 부위가 기대하는 라벨")
    mismatch: bool = Field(description="확신하는데 고른 부위와 다르면 true. 화면이 경고를 띄운다")
    message: str | None = Field(default=None, description="mismatch 일 때 화면에 보일 안내 문구")


class BodyPartHealth(BaseModel):
    model_ready: bool
    error: str | None = None
    min_confidence: float
