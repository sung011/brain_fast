"""
뇌 MRI 5질환 분석 API 라우터 (YOLO26s-Seg).

main.py 에서 prefix="/api/v1" 을 붙인다. 뇌 CT(routers/brain.py)와 별개다.
분석 로직은 class/brain_mri. 이 파일은 받고 넘기기만 한다.

하는 일:
- GET  /api/v1/brain-mri/health   : MRI 모델 설정·로드 상태
- POST /api/v1/analyze/brain-mri  : 8-bit PNG/JPEG MRI 2D slice 1장 → 5질환 검출(bbox, polygon)

prototype inference 다. 질환마다 다른 sequence(T1/T1CE/T1C/DWI/FLAIR)로 학습한 모델이라
임의의 MRI 를 판별하는 범용 모델이 아니다. DICOM 직접 추론은 지원하지 않는다(415).

MRI 모델을 불러오지 못해도 이 주소만 503 이 되고, CT 분석·관리자 기능은 그대로 동작한다.
"""

from __future__ import annotations

import time
import uuid
from importlib import import_module
from types import ModuleType

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from schemas.brainMriSchemas import (
    BrainMriAnalyzeResponse,
    BrainMriHealthResponse,
    BrainMriPrediction,
)

router = APIRouter()


def _load_brain_mri() -> ModuleType:
    try:
        return import_module("class.brain_mri")
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"뇌 MRI 분석 모듈을 불러오지 못했습니다. {exc}",
        ) from exc


@router.get("/brain-mri/health", response_model=BrainMriHealthResponse)
async def brain_mri_health() -> BrainMriHealthResponse:
    try:
        _load_brain_mri()
    except HTTPException as exc:
        return BrainMriHealthResponse(
            status="degraded",
            model="YOLO26s-Seg",
            model_configured=False,
            model_loaded=False,
            model_error=str(exc.detail),
            classes=[],
        )
    config = import_module("class.brain_mri.config")
    constants = import_module("class.brain_mri.constants")
    svc = import_module("class.brain_mri").segmentation_service
    return BrainMriHealthResponse(
        status="ok" if svc.ready else "degraded",
        model=config.MODEL_NAME,
        model_configured=bool(config.MODEL_PATH),
        model_loaded=svc.ready,
        model_error=svc.error,
        classes=list(constants.CLASSES),
        device=svc.device,
    )


@router.post(
    "/analyze/brain-mri",
    response_model=BrainMriAnalyzeResponse,
    summary="뇌 MRI 5질환 prototype inference (8-bit PNG/JPEG 2D slice)",
    description=(
        "지원 전처리·sequence 가 확인된 8-bit PNG/JPEG 2D MRI slice 에 대한 prototype inference. "
        "학습 sequence: 전정신경초종 T1, 신경교종 T1CE, 뇌전이암 T1C, 허혈성 뇌졸중 DWI, 다발성 경화증 FLAIR. "
        "범용 MRI 모델이 아니며 DICOM 은 지원하지 않는다(415). "
        "sequence 는 기록용이며 추론을 바꾸지 않는다. confidence 는 질병 확률이 아니다. 교육·연구용."
    ),
)
async def analyze_brain_mri(
    image: UploadFile = File(...),
    include_overlay: bool = Form(True),
    sequence: str = Form("unknown"),
) -> BrainMriAnalyzeResponse:
    brain_mri = _load_brain_mri()
    config = import_module("class.brain_mri.config")
    constants = import_module("class.brain_mri.constants")
    svc = brain_mri.segmentation_service

    t0 = time.perf_counter()
    data = await image.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="이미지가 너무 큽니다.")
    if len(data) < 64:
        raise HTTPException(status_code=400, detail="유효한 이미지가 아닙니다.")

    seq = next(
        (s for s in constants.SEQUENCES if s.lower() == sequence.strip().lower()), None
    )
    if seq is None:
        raise HTTPException(
            status_code=400,
            detail=f"sequence 는 {constants.SEQUENCES} 중 하나여야 합니다.",
        )

    filename = image.filename or "mri.png"
    if not brain_mri.Preprocess.is_allowed_filename(filename):
        raise HTTPException(
            status_code=415,
            detail="PNG/JPEG 2D MRI slice 만 지원합니다. DICOM 직접 추론은 지원하지 않습니다.",
        )
    try:
        img_uint8 = brain_mri.Preprocess.load_image_uint8_from_bytes(data, filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"이미지 디코딩 실패: {exc}") from exc

    try:
        svc.load()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=svc.error or f"MRI 모델 로드 실패: {exc}",
        ) from exc

    preds = svc.predict(img_uint8)
    h, w = img_uint8.shape[:2]

    overlay_b64 = None
    if include_overlay:
        overlay = brain_mri.Localization.build_overlay_image(img_uint8, preds)
        overlay_b64 = brain_mri.Localization.overlay_to_base64(overlay)

    return BrainMriAnalyzeResponse(
        request_id=f"req_{uuid.uuid4().hex[:12]}",
        model=config.MODEL_LABEL,
        image_width=w,
        image_height=h,
        predictions=[BrainMriPrediction(**p) for p in preds],
        overlay_png_base64=overlay_b64,
        sequence=seq,
        trained_sequences=dict(constants.TRAINED_SEQUENCES),
        input_scope=constants.INPUT_SCOPE,
        limitations=list(constants.LIMITATIONS),
        confidence_note=constants.CONFIDENCE_NOTE,
        disclaimer=constants.DISCLAIMER,
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
