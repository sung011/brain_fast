"""
화면 공유 흉부 X-ray ROI 분석 API 라우터.

main.py 에서 prefix="/api/v1/thorax" 를 붙인다.
실제 주소 예: /api/v1/thorax/health, /api/v1/thorax/analyze/screen-roi
분석 로직은 class/thorax. 응답 형식은 뇌 CT(/api/v1/analyze/screen-roi)와 같다.

하는 일:
- GET  /api/v1/thorax/health              : 흉부 모델 설정·로드 상태
- POST /api/v1/thorax/analyze/screen-roi  : 화면 ROI 이미지에서 소견 박스 탐지·위치 표시

소견마다 박스 탐지 모델이 따로 있다. 소견 점수는 그 소견 박스의 최고 신뢰도다.

판독문(MedGemma)은 아직 없다. 모델이 정해진 뒤 class/thorax/medgemma.py 로 추가한다.
"""

from __future__ import annotations

import base64
import time
import uuid
from importlib import import_module
from types import ModuleType

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from config import settings
from schemas.analyzeSchemas import (
    ClassificationResult,
    FindingItem,
    HealthResponse,
    ImageStats,
    NasUploadInfo,
    RoiInfo,
    ScreenRoiResponse,
)
from services import objectStorageServices as storage_mod

router = APIRouter()
_thorax_mod: ModuleType | None = None
_thorax_error: str | None = None


def _load_thorax() -> ModuleType:
    global _thorax_mod, _thorax_error
    if _thorax_mod is not None:
        return _thorax_mod
    try:
        _thorax_mod = import_module("class.thorax")
        _thorax_error = None
        return _thorax_mod
    except Exception as exc:
        _thorax_error = str(exc)
        raise HTTPException(
            status_code=503,
            detail=f"흉부 분석 모듈을 불러오지 못했습니다. 의존성(torch 등)을 설치하세요. {exc}",
        ) from exc


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    try:
        thorax = _load_thorax()
    except HTTPException:
        return HealthResponse(status="degraded", models_loaded=False, models_error=_thorax_error)
    config = import_module("class.thorax.config")
    service = thorax.detector_service
    error = service.error
    if not config.model_configured():
        error = import_module("class.thorax.ensemble").NOT_CONFIGURED
    return HealthResponse(
        status="ok" if service.ready else "degraded",
        models_loaded=service.ready,
        models_error=error,
        device=str(service.device),
    )


def _ensure_detector(thorax: ModuleType) -> None:
    if thorax.detector_service.ready:
        return
    try:
        thorax.detector_service.load()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=thorax.detector_service.error or f"흉부 모델 로드 실패: {exc}",
        ) from exc


def _build_classification(probs, *, classes: list[str]) -> ClassificationResult:
    constants = import_module("class.thorax.constants")
    findings = [
        FindingItem(label=name, label_ko=constants.class_ko(name), score=float(probs[i]))
        for i, name in enumerate(classes)
    ]
    findings.sort(key=lambda f: f.score, reverse=True)
    top = findings[0]
    return ClassificationResult(
        top_label=top.label,
        top_label_ko=top.label_ko,
        confidence=top.score,
        findings=findings,
        probs=[float(probs[i]) for i in range(len(classes))],
        task="thorax",
    )


async def _maybe_upload_analysis(
    *,
    request_id: str,
    image_bytes: bytes,
    image_filename: str,
    response_payload: dict,
    overlay_b64: str | None,
) -> NasUploadInfo | None:
    """NAS_UPLOAD_ON_ANALYZE 가 켜져 있으면 원본·overlay·result.json 을 올린다 (뇌 CT와 같은 방식)."""
    if not settings.nas_upload_on_analyze:
        return None
    svc = storage_mod.storage_service
    if not svc.enabled:
        return NasUploadInfo(
            ok=False,
            error="NAS_UPLOAD_ON_ANALYZE=true 이지만 Object Storage 설정이 비어 있습니다.",
        )
    try:
        slim = {
            k: v
            for k, v in response_payload.items()
            if k not in ("overlay_png_base64", "nas_upload")
        }
        result = await svc.upload_analysis_bundle(
            request_id=request_id,
            image_bytes=image_bytes,
            image_filename=image_filename,
            result=slim,
            overlay_png_bytes=base64.b64decode(overlay_b64) if overlay_b64 else None,
        )
        files = [
            item.get("remote_path") or item.get("filename") or ""
            for item in result.get("files") or []
        ]
        return NasUploadInfo(
            ok=bool(result.get("ok")),
            folder=result.get("folder"),
            files=[f for f in files if f],
        )
    except Exception as exc:
        return NasUploadInfo(ok=False, error=str(exc))


@router.post("/analyze/screen-roi", response_model=ScreenRoiResponse)
async def analyze_screen_roi(
    image: UploadFile = File(...),
    modality: str = Form("unknown"),
    include_overlay: bool = Form(True),
) -> ScreenRoiResponse:
    thorax = _load_thorax()
    config = import_module("class.thorax.config")
    constants = import_module("class.thorax.constants")
    _ensure_detector(thorax)

    t0 = time.perf_counter()
    data = await image.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="이미지가 너무 큽니다 (최대 5MB).")
    if len(data) < 64:
        raise HTTPException(status_code=400, detail="유효한 이미지가 아닙니다.")

    filename = image.filename or "roi.png"
    try:
        img_uint8 = thorax.Preprocess.load_image_uint8_from_bytes(data, filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"이미지 디코딩 실패: {exc}") from exc

    h, w = img_uint8.shape[:2]
    service = thorax.detector_service
    classes = list(service.classes)
    detections = service.detect(img_uint8)
    classification = _build_classification(service.scores(detections), classes=classes)
    stats = thorax.Preprocess.image_stats(img_uint8)

    _, per_class = thorax.Localization.locate_abnormality(detections, img_uint8)
    overlay_summary = thorax.Localization.summarize_targets(per_class, classes=classes)
    overlay_b64 = None
    if include_overlay:
        overlay = thorax.Localization.build_overlay_image(img_uint8, per_class)
        overlay_b64 = thorax.Localization.overlay_to_base64(overlay)

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    response = ScreenRoiResponse(
        request_id=request_id,
        roi=RoiInfo(width=w, height=h),
        image_stats=ImageStats(**stats),
        classification=classification,
        overlay_png_base64=overlay_b64,
        overlay_summary=overlay_summary,
        source=modality if modality != "unknown" else "screen_capture",
        disclaimer=constants.DISCLAIMER,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        models_loaded=True,
    )
    response.nas_upload = await _maybe_upload_analysis(
        request_id=request_id,
        image_bytes=data,
        image_filename=filename,
        response_payload=response.model_dump(),
        overlay_b64=overlay_b64,
    )
    return response
