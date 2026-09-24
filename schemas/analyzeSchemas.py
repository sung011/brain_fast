"""
API 요청/응답 Pydantic 스키마.

FastAPI가 JSON 직렬화·검증·OpenAPI(/docs) 문서를 자동 생성한다.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class FindingItem(BaseModel):
    """단일 출혈 유형별 분류 점수."""
    label: str
    label_ko: str
    score: float = Field(ge=0.0, le=1.0)


class RoiInfo(BaseModel):
    """업로드 ROI 이미지 픽셀 크기."""
    width: int
    height: int


class ImageStats(BaseModel):
    """ROI 밝기 통계 (디버그·UI 표시용)."""
    mean: float
    std: float


class ClassificationResult(BaseModel):
    """앙상블 분류 결과 — 최상위 라벨 + 전체 findings."""
    top_label: str
    top_label_ko: str
    confidence: float
    findings: list[FindingItem]
    probs: list[float] = Field(description="CLASSES 순서의 원시 확률")
    task: str = Field(default="hemorrhage", description="hemorrhage | germinoma")


class NasUploadInfo(BaseModel):
    """NAS 업로드 결과 요약."""
    ok: bool
    folder: str | None = None
    files: list[str] = Field(default_factory=list)
    error: str | None = None


class ScreenRoiResponse(BaseModel):
    """POST /analyze/screen-roi 응답."""
    request_id: str
    roi: RoiInfo
    image_stats: ImageStats
    classification: ClassificationResult
    germinoma: ClassificationResult | None = None
    overlay_png_base64: str | None = None
    overlay_summary: str | None = None
    source: str = "screen_capture"
    disclaimer: str
    latency_ms: int
    models_loaded: bool
    nas_upload: NasUploadInfo | None = None


class ExplainResponse(BaseModel):
    """POST /analyze/screen-roi/explain 응답 — MedGemma 판독문."""
    request_id: str
    explanation: str
    model: str
    latency_ms: int
    disclaimer: str


class NasHealthResponse(BaseModel):
    """GET /api/v1/nas/health — Synology 연결 상태."""
    ok: bool
    configured: bool
    url: str | None = None
    base_path: str | None = None
    error: str | None = None


class NasUploadResponse(BaseModel):
    """POST /admin/nas/upload 응답 — NAS 업로드 + study 저장."""
    ok: bool
    remote_path: str | None = None
    filename: str | None = None
    bytes: int | None = None
    error: str | None = None
    # study 테이블 저장 결과
    idx: int | None = None
    st_part: str | None = None
    st_modal: str | None = None
    st_disease: str | None = None
    st_image: str | None = None


class HealthResponse(BaseModel):
    """GET /api/v1/health — 모델 로드 상태."""
    status: str
    models_loaded: bool
    models_error: str | None = None
    germinoma_loaded: bool = False
    medgemma_loaded: bool = False
    medgemma_error: str | None = None
    medgemma_device: str | None = None
    device: str | None = None
