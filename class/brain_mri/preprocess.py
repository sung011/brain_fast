"""
뇌 MRI 이미지 전처리 모듈.

업로드 바이트(PNG/JPG/DICOM)를 YOLO 입력용 HxWx3 uint8 배열로 바꾼다.
CT 와 달리 HU 윈도우를 쓰지 않는다. 학습 데이터와 같게 그레이스케일 한 장을 3채널로 복제한다.
DICOM 은 학습 때처럼 슬라이스마다 0.5~99.5 백분위로 0~255 정규화한다.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import pydicom
except ImportError:
    pydicom = None

DICOM_SUFFIXES = {".dcm", ".dicom"}


def normalize_to_uint8(img2d: np.ndarray) -> np.ndarray:
    """MRI 신호 강도 → 0~255 uint8 (0.5~99.5 백분위 클리핑)."""
    arr = np.asarray(img2d, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    lo, hi = np.percentile(finite, [0.5, 99.5])
    if hi <= lo:
        lo, hi = float(finite.min()), float(finite.max())
    if hi <= lo:
        return np.zeros(arr.shape, dtype=np.uint8)
    out = np.clip((np.nan_to_num(arr, nan=lo) - lo) / (hi - lo), 0, 1)
    return (out * 255).astype(np.uint8)


def load_dicom_gray(data: bytes) -> np.ndarray:
    """DICOM 바이트 → 2D 그레이스케일 uint8. 다중 프레임이면 가운데 프레임."""
    if pydicom is None:
        raise RuntimeError("DICOM 처리에 pydicom이 필요합니다.")
    dcm = pydicom.dcmread(io.BytesIO(data), force=True)
    arr = np.asarray(dcm.pixel_array, dtype=np.float32)
    if arr.ndim == 3 and arr.shape[-1] in (3, 4):
        arr = arr[..., :3].mean(axis=-1)
    elif arr.ndim == 3:
        arr = arr[arr.shape[0] // 2]
    slope = float(getattr(dcm, "RescaleSlope", 1.0) or 1.0)
    intercept = float(getattr(dcm, "RescaleIntercept", 0.0) or 0.0)
    return normalize_to_uint8(arr * slope + intercept)


def load_image_uint8_from_bytes(data: bytes, filename: str = "upload.png") -> np.ndarray:
    """png/jpg/dcm bytes → HxWx3 uint8 (그레이스케일 3채널 복제)."""
    if Path(filename).suffix.lower() in DICOM_SUFFIXES:
        gray = load_dicom_gray(data)
    else:
        pil = Image.open(io.BytesIO(data))
        pil.load()
        gray = np.asarray(pil.convert("L"), dtype=np.uint8)
    return np.stack([gray, gray, gray], axis=-1)


class Preprocess:
    """업로드 MRI 바이트를 모델 입력 이미지로 바꾸는 클래스."""

    normalize_to_uint8 = staticmethod(normalize_to_uint8)
    load_dicom_gray = staticmethod(load_dicom_gray)
    load_image_uint8_from_bytes = staticmethod(load_image_uint8_from_bytes)
