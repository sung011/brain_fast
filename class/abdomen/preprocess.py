"""
복부 CT 이미지 전처리.

업로드 바이트(PNG/JPG/DICOM)를 모델 입력용 HxWx3 uint8 배열로 변환한다.
복부 CT 는 soft-tissue 윈도우(WL=40, WW=400)를 쓴다.
화면 캡처는 휘도를 3채널로 복제한다.
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


def apply_window(hu: np.ndarray, wl: float = 40, ww: float = 400) -> np.ndarray:
    """HU 값을 soft-tissue window 로 클리핑 후 0~255 uint8 로 변환."""
    lo = wl - ww / 2
    hi = wl + ww / 2
    img = np.clip(hu, lo, hi)
    img = (img - lo) / (hi - lo + 1e-8)
    return (img * 255.0).astype(np.uint8)


def _decode_pixels(dcm) -> np.ndarray:
    rows, cols = int(dcm.Rows), int(dcm.Columns)
    try:
        return np.asarray(dcm.pixel_array)
    except Exception:
        raw = bytes(dcm.PixelData)
        bits = int(getattr(dcm, "BitsAllocated", 16))
        signed = int(getattr(dcm, "PixelRepresentation", 0)) == 1
        if bits == 8:
            dtype = np.dtype("int8" if signed else "uint8")
        elif bits == 16:
            dtype = np.dtype("<i2" if signed else "<u2")
        else:
            raise ValueError(f"Unsupported BitsAllocated={bits}")
        return np.frombuffer(raw, dtype=dtype, count=rows * cols).reshape(rows, cols)


def load_dicom_hu(data: bytes) -> np.ndarray:
    """DICOM 바이트 → HU(float32) 배열."""
    if pydicom is None:
        raise RuntimeError("DICOM 처리에 pydicom이 필요합니다.")
    dcm = pydicom.dcmread(io.BytesIO(data), force=True)
    img = _decode_pixels(dcm).astype(np.float32)
    slope = float(getattr(dcm, "RescaleSlope", 1.0))
    intercept = float(getattr(dcm, "RescaleIntercept", 0.0))
    return img * slope + intercept


def load_image_uint8_from_bytes(data: bytes, filename: str = "upload.png") -> np.ndarray:
    """png/jpg/dcm bytes → HxWx3 uint8 (soft-tissue 또는 그레이 3채널)."""
    suffix = Path(filename).suffix.lower()
    if suffix in {".dcm", ".dicom"}:
        hu = load_dicom_hu(data)
        gray = apply_window(hu)
        return np.stack([gray, gray, gray], axis=-1)

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
    """화면 공유/DICOM 바이트를 복부 모델 입력 이미지로 바꾸는 클래스."""

    apply_window = staticmethod(apply_window)
    load_dicom_hu = staticmethod(load_dicom_hu)
    load_image_uint8_from_bytes = staticmethod(load_image_uint8_from_bytes)
    image_stats = staticmethod(image_stats)
