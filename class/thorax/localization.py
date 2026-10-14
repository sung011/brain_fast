"""
흉부 X-ray 이상 부위 표시.

탐지 모델이 준 박스를 그대로 위치로 쓴다 (뇌 CT처럼 Grad-CAM으로 추정하지 않는다).
- 소견별 마스크: 그 소견 박스들을 칠한 영역 → ROI 채점에 쓴다
- overlay: 박스 안을 소견 색으로 옅게 칠하고 테두리를 그린 PNG
"""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image, ImageDraw

from .constants import CLASS_COLORS, class_ko
from .ensemble import Detection


def _clip_box(box, w: int, h: int) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = box
    x1, x2 = sorted((int(round(max(0, min(w, x1)))), int(round(max(0, min(w, x2))))))
    y1, y2 = sorted((int(round(max(0, min(h, y1)))), int(round(max(0, min(h, y2))))))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def locate_abnormality(
    detections: list[Detection],
    img_uint8: np.ndarray,
) -> tuple[np.ndarray, dict]:
    """
    박스 → 마스크.

    Returns:
        mask_union (전체 이상 부위), per_class {소견: {"prob", "mask", "boxes"}}
    """
    h, w = img_uint8.shape[:2]
    per_class: dict = {}
    mask_union = np.zeros((h, w), dtype=bool)
    for det in detections:
        box = _clip_box(det.box, w, h)
        if box is None:
            continue
        info = per_class.setdefault(
            det.finding, {"prob": 0.0, "mask": np.zeros((h, w), dtype=bool), "boxes": []}
        )
        x1, y1, x2, y2 = box
        info["mask"][y1:y2, x1:x2] = True
        info["boxes"].append((box, det.confidence))
        info["prob"] = max(info["prob"], det.confidence)
        mask_union[y1:y2, x1:x2] = True
    return mask_union, per_class


def build_overlay_image(img_uint8: np.ndarray, per_class: dict) -> np.ndarray:
    """박스 안을 소견 색으로 옅게 칠하고 테두리를 그린다."""
    base = img_uint8.astype(np.float32) / 255.0
    overlay = base.copy()
    for name, info in per_class.items():
        color = CLASS_COLORS.get(name, (1.0, 0.0, 0.0))
        m = info["mask"]
        for c in range(3):
            overlay[m, c] = overlay[m, c] * 0.65 + color[c] * 0.35
    img = Image.fromarray((np.clip(overlay, 0, 1) * 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    width = max(2, round(min(img.size) / 300))
    for name, info in per_class.items():
        rgb = tuple(int(v * 255) for v in CLASS_COLORS.get(name, (1.0, 0.0, 0.0)))
        for (x1, y1, x2, y2), _conf in info["boxes"]:
            draw.rectangle([x1, y1, x2 - 1, y2 - 1], outline=rgb, width=width)
    return np.asarray(img)


def overlay_to_base64(overlay: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(overlay).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def summarize_targets(per_class: dict, *, classes: list[str]) -> str:
    """
    UI용 한 줄 요약 (예: '흉막삼출 (신뢰도 0.42, 하부-환자 좌측, 박스 1개)').

    흉부 PA 영상은 화면 왼쪽이 환자의 오른쪽이다. 좌우는 환자 기준으로 적는다.
    """
    bits = []
    for name in classes:
        info = per_class.get(name)
        if not info or not info["mask"].any():
            continue
        ys, xs = np.where(info["mask"])
        cy, cx = float(np.mean(ys)), float(np.mean(xs))
        h, w = info["mask"].shape
        vert = "상부" if cy < h / 3 else ("하부" if cy > 2 * h / 3 else "중부")
        side = "환자 우측" if cx < w / 3 else ("환자 좌측" if cx > 2 * w / 3 else "중앙")
        bits.append(
            f"{class_ko(name)} (신뢰도 {info['prob']:.2f}, {vert}-{side}, 박스 {len(info['boxes'])}개)"
        )
    return "; ".join(bits) if bits else "강조된 이상 부위 없음"


class Localization:
    """박스 기반 이상 부위 표시 클래스."""

    locate_abnormality = staticmethod(locate_abnormality)
    build_overlay_image = staticmethod(build_overlay_image)
    overlay_to_base64 = staticmethod(overlay_to_base64)
    summarize_targets = staticmethod(summarize_targets)
