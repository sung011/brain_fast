"""
뇌 MRI 5질환 분석 요청/응답 Pydantic 스키마.

뇌 CT(analyzeSchemas.py)와 응답 형식이 다르다.
CT 는 분류 확률, MRI 는 YOLO 세그멘테이션 검출 목록(bbox + polygon)이다.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BrainMriPrediction(BaseModel):
    """검출 1건. 좌표는 업로드 이미지 픽셀 기준."""
    class_id: int = Field(ge=0, le=4, description="클래스 번호 (0~4)")
    disease: str = Field(description="영문 클래스명")
    disease_ko: str = Field(description="한글 클래스명")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Model confidence score (0~1). 질병 확률이 아님."
    )
    bbox: list[float] = Field(
        min_length=4, max_length=4, description="[x1, y1, x2, y2]"
    )
    polygon: list[list[float]] = Field(
        default_factory=list, description="세그멘테이션 외곽선 [[x, y], ...]"
    )


class BrainMriAnalyzeResponse(BaseModel):
    """POST /api/v1/analyze/brain-mri 응답. 검출이 없으면 predictions 는 빈 배열."""
    request_id: str
    modality: str = "mri"
    model: str
    image_width: int
    image_height: int
    predictions: list[BrainMriPrediction]
    overlay_png_base64: str | None = None
    sequence: str = Field(
        default="unknown", description="요청에 적은 MRI sequence. 추론 방식은 바꾸지 않고 기록만 한다."
    )
    trained_sequences: dict[str, str] = Field(description="질환별 학습 sequence")
    input_scope: str
    limitations: list[str]
    confidence_note: str
    is_diagnosis: bool = False
    disclaimer: str
    latency_ms: int


class BrainMriHealthResponse(BaseModel):
    """GET /api/v1/brain-mri/health — MRI 모델 로드 상태."""
    status: str
    model: str
    model_configured: bool
    model_loaded: bool
    model_error: str | None = None
    classes: list[str]
    device: str | None = None
