"""
뇌 MRI 검출 결과 overlay.

YOLO 가 돌려준 polygon 을 반투명 색으로 칠하고 bbox 와 라벨을 그린다.
Grad-CAM 은 쓰지 않는다 (세그멘테이션 모델이 외곽선을 직접 준다).
"""
from __future__ import annotations

import base64

import cv2
import numpy as np

from .constants import CLASS_COLORS


def _bgr(name: str) -> tuple[int, int, int]:
    r, g, b = CLASS_COLORS.get(name, (1.0, 1.0, 1.0))
    return int(b * 255), int(g * 255), int(r * 255)


def _points(polygon: list[list[float]]) -> np.ndarray:
    return np.round(np.asarray(polygon, dtype=np.float32)).astype(np.int32)


def build_overlay_image(
    img_uint8: np.ndarray, predictions: list[dict], alpha: float = 0.35
) -> np.ndarray:
    """HxWx3 uint8(RGB) + 검출 목록 → overlay RGB uint8. 검출이 없으면 원본 그대로."""
    base = cv2.cvtColor(np.ascontiguousarray(img_uint8), cv2.COLOR_RGB2BGR)
    fill = base.copy()
    for p in predictions:
        if len(p.get("polygon") or []) >= 3:
            cv2.fillPoly(fill, [_points(p["polygon"])], _bgr(p["disease"]))
    out = cv2.addWeighted(fill, alpha, base, 1 - alpha, 0)
    for p in predictions:
        color = _bgr(p["disease"])
        if len(p.get("polygon") or []) >= 3:
            cv2.polylines(out, [_points(p["polygon"])], True, color, 1, cv2.LINE_AA)
        x1, y1, x2, y2 = (int(round(v)) for v in p["bbox"])
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 1)
        label = "{} {:.2f}".format(p["disease"], p["confidence"])
        cv2.putText(
            out, label, (x1, max(12, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA,
        )
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)


def overlay_to_base64(overlay_rgb: np.ndarray) -> str:
    """RGB uint8 → PNG base64 문자열 (data: 접두사 없음)."""
    ok, buf = cv2.imencode(".png", cv2.cvtColor(overlay_rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError("overlay PNG 인코딩 실패")
    return base64.b64encode(buf.tobytes()).decode("ascii")


class Localization:
    """검출 결과를 이미지 위에 표시하는 클래스."""

    build_overlay_image = staticmethod(build_overlay_image)
    overlay_to_base64 = staticmethod(overlay_to_base64)
