"""
뇌 MRI 이미지 전처리 모듈.

이미 2D slice 로 만들어진 8-bit PNG/JPEG 를 YOLO 입력용 HxWx3 uint8 배열로 바꾼다.
학습 데이터 PNG 는 통합 단계에서 그대로 복사됐으므로, 같은 형식의 8-bit 그레이스케일 slice 는
추가 정규화 없이 3채널로 복제만 한다. CT HU 윈도우는 쓰지 않는다.

DICOM 은 받지 않는다. 학습 PNG 생성 방식이 질환마다 달라서 한 장의 DICOM 으로 재현할 수 없다.
- VS(T1, DICOM)·glioma(T1CE, NIfTI): 3D 볼륨 전체 기준 0.5~99.5 백분위
- metastasis(T1C)·stroke(DWI): slice 기준 0.5~99.5 백분위
- MS(FLAIR): slice 기준 1~99 백분위
- VS 외 4질환은 NIfTI 배열 축 그대로 저장돼 DICOM 표시 방향과 다를 수 있다
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg"}
# 8-bit 모드만 받는다. 16-bit·float PNG 는 학습 데이터와 값 범위가 달라 거절한다.
ALLOWED_MODES = {"L", "LA", "RGB", "RGBA", "P"}


class UnsupportedImageError(ValueError):
    """지원하지 않는 파일 형식(확장자). 라우터에서 415 로 바꾼다."""


def is_allowed_filename(filename: str | None) -> bool:
    return bool(filename) and Path(filename).suffix.lower() in ALLOWED_SUFFIXES


def load_image_uint8_from_bytes(data: bytes, filename: str = "upload.png") -> np.ndarray:
    """8-bit png/jpg bytes → HxWx3 uint8 (그레이스케일 3채널 복제)."""
    if not is_allowed_filename(filename):
        raise UnsupportedImageError(
            "PNG/JPEG 2D MRI slice 만 지원합니다. DICOM 직접 추론은 지원하지 않습니다."
        )
    try:
        pil = Image.open(io.BytesIO(data))
        pil.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("이미지를 디코딩할 수 없습니다.") from exc
    if pil.mode not in ALLOWED_MODES:
        raise ValueError(
            f"8-bit 이미지만 지원합니다 (현재 모드 {pil.mode}). 0~255 로 변환한 slice 를 올리세요."
        )
    gray = np.asarray(pil.convert("L"), dtype=np.uint8)
    return np.stack([gray, gray, gray], axis=-1)


class Preprocess:
    """업로드 MRI slice 바이트를 모델 입력 이미지로 바꾸는 클래스."""

    is_allowed_filename = staticmethod(is_allowed_filename)
    load_image_uint8_from_bytes = staticmethod(load_image_uint8_from_bytes)
