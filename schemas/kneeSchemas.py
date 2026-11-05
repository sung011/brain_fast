"""
무릎 MRI 화면 캡처 한 장 분석 API 의 응답·오류 형식.

뇌 CT 스키마(analyzeSchemas.py)와 공유하지 않는다. 소견마다 "평가함/평가하지 않음"을 분명히 나눠 담는다.
HTTP 200 은 요청 처리 성공이지 진단 성공이 아니다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KneeNotice(BaseModel):
    code: str
    text: str


class KneeImageInfo(BaseModel):
    width: int
    height: int
    plane: Literal["axial", "coronal", "sagittal"]
    image_scope: Literal["full_slice", "roi_crop"]
    source: Literal["screen_capture"] = "screen_capture"


class KneeFinding(BaseModel):
    """소견 하나. 평가하지 않은 소견은 prob·confidence_band·verdict 가 모두 null 이다."""
    label_ko: str
    status: Literal["evaluated", "not_evaluated"]
    reason_code: str | None = None
    reason: str | None = None
    prob: float | None = Field(default=None, ge=0.0, le=1.0, description="모델 점수. 임상적 발병 확률이 아니다.")
    score_label: str | None = None
    confidence_band: Literal["confident", "uncertain"] | None = None
    verdict: str | None = None
    planes_used: list[str] = Field(default_factory=list)
    missing_planes: list[str] = Field(default_factory=list)


class KneeAclBox(BaseModel):
    label: str
    x_center: float = Field(ge=0.0, le=1.0)
    y_center: float = Field(ge=0.0, le=1.0)
    width: float = Field(ge=0.0, le=1.0)
    height: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class KneeVisualization(BaseModel):
    gradcam_status: Literal["shown", "no_activation", "not_evaluated"]
    gradcam_finding: str | None = None
    overlay_png_base64: str | None = None
    overlay_note: str | None = None
    acl_location_status: Literal["shown", "not_found", "not_evaluated"]
    acl_box: KneeAclBox | None = None
    acl_box_note: str | None = None


class KneeValidated(BaseModel):
    finding: str
    validation_id: str
    validated_condition: str
    model_id: str
    performance: dict


class KneeEvidence(BaseModel):
    gates_version: str
    validated: list[KneeValidated] = Field(default_factory=list)


class KneeExplanation(BaseModel):
    finding: str
    id: str
    version: str
    text: str


class KneeRetention(BaseModel):
    input_deleted: bool
    transient_only: bool
    scope_note: str


class KneeScreenRoiResponse(BaseModel):
    """POST /api/v1/knee/screen-roi 응답."""
    request_id: str
    body_part: Literal["knee"] = "knee"
    image: KneeImageInfo
    analysis_status: Literal["evaluated", "partially_evaluated", "not_evaluated"]
    findings: dict[str, KneeFinding]
    visualization: KneeVisualization
    notices: list[KneeNotice]
    evidence: KneeEvidence
    explanations: list[KneeExplanation] = Field(default_factory=list)
    retention: KneeRetention
    latency_ms: int
    models_loaded: bool


class KneeErrorBody(BaseModel):
    """모든 오류의 본문. 업로드 값·파일 이름·예외 원문·모델 저장소 정보를 넣지 않는다."""
    request_id: str
    code: str
    message: str
    retryable: bool
