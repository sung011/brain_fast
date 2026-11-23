"""
화면 공유 복부 CT ROI 분석 API 라우터.

main.py 에서 prefix="/api/v1/abdomen" 를 붙인다.
실제 주소 예: /api/v1/abdomen/health, /api/v1/abdomen/analyze/screen-roi
분석 로직은 class/abdomen. 응답 형식은 뇌 CT(/api/v1/analyze/screen-roi)와 같다.

하는 일:
- GET  /api/v1/abdomen/health                      : 복부 모델·판독문 로드 상태
- POST /api/v1/abdomen/analyze/screen-roi          : 화면 ROI 에서 장기 박스 탐지·위치 표시
- POST /api/v1/abdomen/analyze/screen-roi/explain  : 검출 결과로 한국어 판독문 생성

장기마다 점수는 그 장기 박스의 최고 신뢰도다. 질환 진단이 아니라 장기 위치 학습용이다.
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
from services import objectStorageServices as storage_mod

router = APIRouter()
_abdomen_mod: ModuleType | None = None
_abdomen_error: str | None = None


def _load_abdomen() -> ModuleType:
    global _abdomen_mod, _abdomen_error
    if _abdomen_mod is not None:
        return _abdomen_mod
    try:
        _abdomen_mod = import_module("class.abdomen")
        _abdomen_error = None
        return _abdomen_mod
    except Exception as exc:
        _abdomen_error = str(exc)
        raise HTTPException(
            status_code=503,
            detail=f"복부 분석 모듈을 불러오지 못했습니다. 의존성(torch·ultralytics 등)을 설치하세요. {exc}",
        ) from exc


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    try:
        abdomen = _load_abdomen()
    except HTTPException:
        return HealthResponse(status="degraded", models_loaded=False, models_error=_abdomen_error)
    config = import_module("class.abdomen.config")
    service = abdomen.detector_service
    if not service.ready and config.model_configured():
        try:
            service.load()
        except Exception:
            pass
    if not abdomen.medgemma_service.ready:
        try:
            abdomen.medgemma_service.load()
        except Exception:
            pass
    error = service.error
    if not config.model_configured():
        error = import_module("class.abdomen.ensemble").NOT_CONFIGURED
    return HealthResponse(
        status="ok" if service.ready else "degraded",
        models_loaded=service.ready,
        models_error=error,
        medgemma_loaded=abdomen.medgemma_service.ready,
        medgemma_error=abdomen.medgemma_service.error,
        medgemma_device=abdomen.medgemma_service.device,
        device=str(service.device),
    )


def _ensure_detector(abdomen: ModuleType) -> None:
    if abdomen.detector_service.ready:
        return
    try:
        abdomen.detector_service.load()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=abdomen.detector_service.error or f"복부 모델 로드 실패: {exc}",
        ) from exc


def _build_classification(probs, *, classes: list[str]) -> ClassificationResult:
    constants = import_module("class.abdomen.constants")
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
        task="abdomen",
    )


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
        overlay_bytes = None
        if overlay_b64:
            raw = overlay_b64.strip()
            if "," in raw and raw.lower().startswith("data:"):
                raw = raw.split(",", 1)[1]
            try:
                overlay_bytes = base64.b64decode(raw)
            except Exception:
                overlay_bytes = None
        result = await svc.upload_analysis_bundle(
            request_id=request_id,
            image_bytes=image_bytes,
            image_filename=image_filename,
            result=slim,
            overlay_png_bytes=overlay_bytes,
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
    abdomen = _load_abdomen()
    config = import_module("class.abdomen.config")
    constants = import_module("class.abdomen.constants")
    _ensure_detector(abdomen)

    t0 = time.perf_counter()
    data = await image.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="이미지가 너무 큽니다 (최대 5MB).")
    if len(data) < 64:
        raise HTTPException(status_code=400, detail="유효한 이미지가 아닙니다.")

    filename = image.filename or "roi.png"
    try:
        img_uint8 = abdomen.Preprocess.load_image_uint8_from_bytes(data, filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"이미지 디코딩 실패: {exc}") from exc

    h, w = img_uint8.shape[:2]
    service = abdomen.detector_service
    classes = list(service.classes)
    detections = service.detect(img_uint8)
    classification = _build_classification(service.scores(detections), classes=classes)
    stats = abdomen.Preprocess.image_stats(img_uint8)

    _, per_class = abdomen.Localization.locate_abnormality(detections, img_uint8)
    overlay_summary = abdomen.Localization.summarize_targets(per_class, classes=classes)
    overlay_b64 = None
    if include_overlay:
        overlay = abdomen.Localization.build_overlay_image(img_uint8, per_class)
        overlay_b64 = abdomen.Localization.overlay_to_base64(overlay)

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


@router.post("/analyze/screen-roi/explain", response_model=ExplainResponse)
async def explain_screen_roi(
    image: UploadFile = File(...),
    probs_json: str = Form(...),
    overlay_png_base64: str = Form(""),
) -> ExplainResponse:
    abdomen = _load_abdomen()
    config = import_module("class.abdomen.config")
    constants = import_module("class.abdomen.constants")

    t0 = time.perf_counter()
    data = await image.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="이미지가 너무 큽니다.")

    n_cls = len(constants.CLASSES)
    try:
        probs = json.loads(probs_json)
        if not isinstance(probs, list) or len(probs) != n_cls:
            raise ValueError(f"probs_json must be a list of {n_cls} floats")
        probs = [float(p) for p in probs]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"probs_json 파싱 실패: {exc}") from exc

    _ensure_detector(abdomen)

    try:
        img_uint8 = abdomen.Preprocess.load_image_uint8_from_bytes(
            data, image.filename or "roi.png"
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"이미지 디코딩 실패: {exc}") from exc

    # explain 은 전달받은 probs 로 판독문을 쓰고, overlay 는 다시 검출하거나 클라이언트가 준 PNG 를 쓴다.
    detections = abdomen.detector_service.detect(img_uint8)
    _, per_class = abdomen.Localization.locate_abnormality(detections, img_uint8)
    overlay_uint8 = abdomen.Localization.build_overlay_image(img_uint8, per_class)

    if overlay_png_base64.strip():
        try:
            overlay_uint8 = abdomen.medgemma_service.decode_overlay_base64(
                overlay_png_base64.strip()
            )
        except Exception:
            pass

    try:
        explanation = abdomen.medgemma_service.explain(
            original_uint8=img_uint8,
            overlay_uint8=overlay_uint8,
            probs=probs,
            per_class=per_class,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    latency_ms = int((time.perf_counter() - t0) * 1000)
    return ExplainResponse(
        request_id=f"req_{uuid.uuid4().hex[:12]}",
        explanation=explanation,
        model=config.MEDGEMMA_MODEL_ID if abdomen.medgemma_service.backend != "template" else "abdomen-template",
        latency_ms=latency_ms,
        disclaimer=constants.DISCLAIMER,
    )
