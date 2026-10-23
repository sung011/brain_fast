"""
부위 자동 판별 업무 규칙.

사진이 어떤 부위인지 판별 모델에 묻고, 화면에서 고른 부위와 비교해 경고가 필요한지 정한다.
판별 모델이 없거나 확신이 낮거나 "알 수 없음"이면 경고하지 않는다(멀쩡한 사진에 헛경고를 내지 않기 위해).
분석(뇌·흉부 모델)은 여기서 하지 않는다. 판별 로직은 class/bodypart.
"""
from __future__ import annotations

import io
from importlib import import_module

import numpy as np
from PIL import Image


def decode_image(data: bytes) -> np.ndarray:
    """업로드 바이트 → 회색조 uint8 배열. 읽을 수 없으면 ValueError."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return np.asarray(img.convert("L"), dtype=np.uint8)
    except Exception as exc:
        raise ValueError(f"이미지 디코딩 실패: {exc}") from exc


def check(data: bytes, selected: str | None) -> dict:
    """사진 → 판별 결과와 경고 여부. selected 는 화면 드롭다운 값(brain, thorax …)."""
    bodypart = import_module("class.bodypart")
    config = import_module("class.bodypart.config")

    img = decode_image(data)
    service = bodypart.classifier_service
    label, confidence = service.predict(img)
    expected = bodypart.DROPDOWN_TO_LABEL.get((selected or "").strip())

    mismatch = bool(
        service.ready
        and expected
        and label != bodypart.UNKNOWN
        and confidence >= config.MIN_CONFIDENCE
        and label != expected
    )
    message = None
    if mismatch:
        message = (
            f"선택한 부위({bodypart.label_ko(expected)})와 사진이 달라 보입니다"
            f"({bodypart.label_ko(label)}로 추정). 부위를 확인하세요."
        )
    return {
        "label": label,
        "label_ko": bodypart.label_ko(label),
        "confidence": confidence,
        "model_ready": service.ready,
        "selected": selected or None,
        "expected": expected,
        "mismatch": mismatch,
        "message": message,
    }


def health() -> dict:
    """판별 모델 준비 상태. 모델을 읽어 보려고 시도하지만 실패해도 서버는 멈추지 않는다."""
    bodypart = import_module("class.bodypart")
    config = import_module("class.bodypart.config")
    service = bodypart.classifier_service
    if not service.ready:
        try:
            service.load()
        except bodypart.BodyPartNotReady:
            pass
    return {"model_ready": service.ready, "error": service.error, "min_confidence": config.MIN_CONFIDENCE}
