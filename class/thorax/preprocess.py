"""
흉부 X-ray 전처리 모듈.

업로드 바이트(PNG/JPG/DICOM)를 모델 입력용 HxWx3 uint8 배열로 변환한다.
X-ray는 CT처럼 HU 윈도우를 쓰지 않는다. 흑백 밝기를 3채널로 복제한다.
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

try:  # pydicom 3.x
    from pydicom.pixels import apply_voi_lut
except ImportError:
    try:  # pydicom 2.x
        from pydicom.pixel_data_handlers.util import apply_voi_lut
    except ImportError:
        apply_voi_lut = None


def _to_uint8(arr: np.ndarray) -> np.ndarray:
    """1~99 백분위로 잘라 0~255로 늘린다. 가장자리 글자·검은 여백의 영향을 줄인다."""
    arr = arr.astype(np.float32)
    lo, hi = np.percentile(arr, 1), np.percentile(arr, 99)
    if hi <= lo:
        lo, hi = float(arr.min()), float(arr.max())
    if hi <= lo:
        return np.zeros(arr.shape, dtype=np.uint8)
    arr = np.clip((arr - lo) / (hi - lo), 0.0, 1.0)
    return (arr * 255.0).astype(np.uint8)


def load_dicom_gray(data: bytes) -> np.ndarray:
    """DICOM 바이트 → 흑백 uint8. MONOCHROME1(흰 배경)은 반전해 MONOCHROME2로 맞춘다."""
    if pydicom is None:
        raise RuntimeError("DICOM 처리에 pydicom이 필요합니다.")
    dcm = pydicom.dcmread(io.BytesIO(data), force=True)
    arr = np.asarray(dcm.pixel_array)
    if apply_voi_lut is not None:
        try:
            arr = apply_voi_lut(arr, dcm)
        except Exception:
            pass
    if arr.ndim == 3:
        arr = arr[..., 0] if arr.shape[-1] in (3, 4) else arr[0]
    gray = _to_uint8(arr)
    if str(getattr(dcm, "PhotometricInterpretation", "")).upper() == "MONOCHROME1":
        gray = 255 - gray
    return gray


def load_image_uint8_from_bytes(data: bytes, filename: str = "upload.png") -> np.ndarray:
    """png/jpg/dcm bytes → HxWx3 uint8 (흑백 3채널)."""
    suffix = Path(filename).suffix.lower()
    if suffix in {".dcm", ".dicom"}:
        gray = load_dicom_gray(data)
    else:
        pil = Image.open(io.BytesIO(data))
        gray = np.asarray(pil.convert("L"), dtype=np.uint8)
    return np.stack([gray, gray, gray], axis=-1)


def image_stats(img_uint8: np.ndarray) -> dict[str, float]:
    """첫 번째 채널 기준 평균·표준편차 (UI 표시용)."""
    gray = img_uint8[..., 0].astype(np.float32)
    return {
        "mean": round(float(gray.mean()), 1),
        "std": round(float(gray.std()), 1),
    }


class Preprocess:
    """화면 공유/DICOM 바이트를 흉부 모델 입력 이미지로 바꾸는 클래스."""

    load_dicom_gray = staticmethod(load_dicom_gray)
    load_image_uint8_from_bytes = staticmethod(load_image_uint8_from_bytes)
    image_stats = staticmethod(image_stats)
