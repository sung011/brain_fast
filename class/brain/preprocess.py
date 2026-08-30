"""
이미지 전처리 모듈.

업로드 바이트(PNG/JPG/DICOM)를 모델 입력용 HxWx3 uint8 배열로 변환한다.
화면 캡처는 그레이스케일 휘도를 CT 윈도우 레이아웃(brain/subdural)에 맞게 3채널로 구성.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from .config import WINDOW_MODE

try:
    import pydicom
except ImportError:
    pydicom = None


def apply_window(hu: np.ndarray, wl: float = 40, ww: float = 80) -> np.ndarray:
    """HU 값을 window level/width로 클리핑 후 0~255 uint8로 변환."""
    lo = wl - ww / 2
    hi = wl + ww / 2
    img = np.clip(hu, lo, hi)
    img = (img - lo) / (hi - lo + 1e-8)
    return (img * 255.0).astype(np.uint8)


def make_3channel_windows(hu: np.ndarray) -> np.ndarray:
    """뇌/경막하/골 CT 윈도우 3채널 스택."""
    brain = apply_window(hu, wl=40, ww=80)
    subdural = apply_window(hu, wl=80, ww=200)
    bone = apply_window(hu, wl=600, ww=2800)
    return np.stack([brain, subdural, bone], axis=-1)


def apply_window_layout(img3: np.ndarray, mode: str = WINDOW_MODE) -> np.ndarray:
    """3채널 윈도우를 모델이 기대하는 RGB 배치로 재구성."""
    brain, subdural, bone = img3[..., 0], img3[..., 1], img3[..., 2]
    if mode == "brain_subdural":
        return np.stack([brain, subdural, brain], axis=-1)
    if mode == "brain":
        return np.stack([brain, brain, brain], axis=-1)
    return img3


def _decode_pixels(dcm) -> np.ndarray:
    """pydicom 객체에서 픽셀 배열 추출 (압축/비표준 포맷 fallback)."""
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
    """
    png/jpg/dcm bytes → HxWx3 uint8 (window layout 적용).

    화면 공유 스크린샷: RGB 대신 L 채널 3번 복제 후 brain_subdural 레이아웃.
    """
    suffix = Path(filename).suffix.lower()

    if suffix in {".dcm", ".dicom"}:
        hu = load_dicom_hu(data)
        img = make_3channel_windows(hu)
        return apply_window_layout(img, WINDOW_MODE)

    pil = Image.open(io.BytesIO(data))
    if pil.mode != "RGB":
        pil = pil.convert("RGB")
    arr = np.asarray(pil, dtype=np.uint8)

    # 화면 공유 스크린샷: 휘도를 CT 윈도우 프록시로 사용
    gray = np.asarray(pil.convert("L"), dtype=np.uint8)
    img3 = np.stack([gray, gray, gray], axis=-1)
    return apply_window_layout(img3, WINDOW_MODE)


def image_stats(img_uint8: np.ndarray) -> dict[str, float]:
    """첫 번째 채널 기준 평균·표준편차 (UI 표시용)."""
    gray = img_uint8[..., 0].astype(np.float32)
    return {
        "mean": round(float(gray.mean()), 1),
        "std": round(float(gray.std()), 1),
    }


class Preprocess:
    """화면 공유/DICOM 바이트를 모델 입력 이미지로 바꾸는 클래스."""

    apply_window = staticmethod(apply_window)
    make_3channel_windows = staticmethod(make_3channel_windows)
    apply_window_layout = staticmethod(apply_window_layout)
    load_dicom_hu = staticmethod(load_dicom_hu)
    load_image_uint8_from_bytes = staticmethod(load_image_uint8_from_bytes)
    image_stats = staticmethod(image_stats)
