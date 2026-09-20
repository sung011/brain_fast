"""
화면 공유 ROI 분석 API 라우터.

main.py 에서 prefix="/api/v1" 을 붙인다.
실제 주소 예: /api/v1/health, /api/v1/analyze/screen-roi
원본: MediLens 화면공유분석. 분석 로직은 class/brain.

하는 일:
- GET  /api/v1/health                      : 앙상블·MedGemma 모델 로드 상태
- POST /api/v1/analyze/screen-roi          : 화면 ROI 이미지 분류·소견
- POST /api/v1/analyze/screen-roi/explain  : 분류 결과로 판독문(설명) 생성
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from importlib import import_module
from types import ModuleType

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from config import settings
from schemas.analyzeSchemas import (
    ClassificationResult,
    ExplainResponse,
    FindingItem,
    HealthResponse,
    ImageStats,
    NasUploadInfo,
    RoiInfo,
    ScreenRoiResponse,
)
from services import nasServices as nas_service_mod

router = APIRouter()
_brain_mod: ModuleType | None = None
_brain_error: str | None = None


def _load_brain() -> ModuleType:
    global _brain_mod, _brain_error
    if _brain_mod is not None:
        return _brain_mod
    try:
        _brain_mod = import_module("class.brain")
        _brain_error = None
        return _brain_mod
    except Exception as exc:
        _brain_error = str(exc)
        raise HTTPException(
            status_code=503,
            detail=f"뇌 분석 모듈을 불러오지 못했습니다. 의존성(torch 등)을 설치하세요. {exc}",
        ) from exc


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    try:
        brain = _load_brain()
    except HTTPException:
        return HealthResponse(
            status="degraded",
            models_loaded=False,
            models_error=_brain_error,
        )
    return HealthResponse(
        status="ok" if brain.ensemble_service.ready else "degraded",
        models_loaded=brain.ensemble_service.ready,
        models_error=brain.ensemble_service.error,
        medgemma_loaded=brain.medgemma_service.ready,
        medgemma_error=brain.medgemma_service.error,
        medgemma_device=brain.medgemma_service.device,
        device=str(brain.ensemble_service.device),
    )


def _ensure_ensemble(brain: ModuleType) -> None:
    if brain.ensemble_service.ready:
        return
    try:
        brain.ensemble_service.load()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=brain.ensemble_service.error or f"앙상블 모델 로드 실패: {exc}",
        ) from exc


def _build_classification(brain: ModuleType, probs) -> ClassificationResult:
    constants = import_module("class.brain.constants")
    findings = [
        FindingItem(
            label=name,
            label_ko=constants.class_ko(name),
            score=float(probs[i]),
        )
        for i, name in enumerate(constants.CLASSES)
    ]
    findings.sort(key=lambda f: f.score, reverse=True)
    top = findings[0]
    return ClassificationResult(
        top_label=top.label,
        top_label_ko=top.label_ko,
        confidence=top.score,
        findings=findings,
        probs=[float(probs[i]) for i in range(len(constants.CLASSES))],
    )


def _overlay_b64_to_bytes(overlay_b64: str | None) -> bytes | None:
    if not overlay_b64:
        return None
    raw = overlay_b64.strip()
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw)
    except Exception:
        return None


async def _maybe_upload_analysis(
    *,
    request_id: str,
    image_bytes: bytes,
    image_filename: str,
    response_payload: dict,
    overlay_b64: str | None,
) -> NasUploadInfo | None:
    if not settings.nas_upload_on_analyze:
        return None
    svc = nas_service_mod.nas_service
    if not svc.enabled:
        return NasUploadInfo(
            ok=False,
            error="NAS_UPLOAD_ON_ANALYZE=true 이지만 NAS 계정 설정이 비어 있습니다.",
        )
    try:
        # base64 overlay는 NAS에 중복 저장하지 않도록 JSON에서 제외
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
            overlay_png_bytes=_overlay_b64_to_bytes(overlay_b64),
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
    brain = _load_brain()
    config = import_module("class.brain.config")
    constants = import_module("class.brain.constants")
    _ensure_ensemble(brain)

    t0 = time.perf_counter()
    data = await image.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="이미지가 너무 큽니다 (최대 5MB).")
    if len(data) < 64:
        raise HTTPException(status_code=400, detail="유효한 이미지가 아닙니다.")

    filename = image.filename or "roi.png"
    try:
        img_uint8 = brain.Preprocess.load_image_uint8_from_bytes(data, filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"이미지 디코딩 실패: {exc}") from exc

    h, w = img_uint8.shape[:2]
    probs = brain.ensemble_service.predict(img_uint8)
    classification = _build_classification(brain, probs)
    stats = brain.Preprocess.image_stats(img_uint8)

    overlay_b64 = None
    overlay_summary = None
    if include_overlay:
        _, _, per_class, targets = brain.Localization.locate_abnormality(
            brain.ensemble_service, img_uint8, probs
        )
        overlay = brain.Localization.build_overlay_image(img_uint8, per_class)
        overlay_b64 = brain.Localization.overlay_to_base64(overlay)
        overlay_summary = brain.Localization.summarize_targets(per_class, targets)

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    latency_ms = int((time.perf_counter() - t0) * 1000)
    response = ScreenRoiResponse(
        request_id=request_id,
        roi=RoiInfo(width=w, height=h),
        image_stats=ImageStats(**stats),
        classification=classification,
        overlay_png_base64=overlay_b64,
        overlay_summary=overlay_summary,
        source=modality if modality != "unknown" else "screen_capture",
        disclaimer=constants.DISCLAIMER,
        latency_ms=latency_ms,
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


@router.post("/analyze/screen-roi/explain", response_model=ExplainResponse)
async def explain_screen_roi(
    image: UploadFile = File(...),
    probs_json: str = Form(...),
    overlay_png_base64: str = Form(""),
) -> ExplainResponse:
    brain = _load_brain()
    config = import_module("class.brain.config")
    constants = import_module("class.brain.constants")

    t0 = time.perf_counter()
    data = await image.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="이미지가 너무 큽니다.")

    try:
        probs = json.loads(probs_json)
        if not isinstance(probs, list) or len(probs) != 6:
            raise ValueError("probs_json must be a list of 6 floats")
        probs = [float(p) for p in probs]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"probs_json 파싱 실패: {exc}") from exc

    _ensure_ensemble(brain)

    try:
        img_uint8 = brain.Preprocess.load_image_uint8_from_bytes(
            data, image.filename or "roi.png"
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"이미지 디코딩 실패: {exc}") from exc

    import numpy as np

    probs_arr = np.array(probs, dtype=np.float32)
    _, _, per_class, targets = brain.Localization.locate_abnormality(
        brain.ensemble_service, img_uint8, probs_arr
    )
    overlay_uint8 = brain.Localization.build_overlay_image(img_uint8, per_class)

    if overlay_png_base64.strip():
        try:
            overlay_uint8 = brain.medgemma_service.decode_overlay_base64(
                overlay_png_base64.strip()
            )
        except Exception:
            pass

    try:
        explanation = brain.medgemma_service.explain(
            original_uint8=img_uint8,
            overlay_uint8=overlay_uint8,
            probs=probs,
            per_class=per_class,
            targets=targets,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    latency_ms = int((time.perf_counter() - t0) * 1000)
    return ExplainResponse(
        request_id=f"req_{uuid.uuid4().hex[:12]}",
        explanation=explanation,
        model=config.MEDGEMMA_MODEL_ID,
        latency_ms=latency_ms,
        disclaimer=constants.DISCLAIMER,
    )
