"""
복부 CT 장기 박스 overlay.

탐지 모델이 준 박스를 위치로 쓴다. Grad-CAM 은 쓰지 않는다.
같은 장기에서 겹치는 박스는 하나로 합치고, 장기마다 점수 높은 MAX_REGIONS 곳만 남긴다.
"""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .constants import CLASS_COLORS, class_ko
from .ensemble import Detection

BOX_LABELS = {
    "adrenal_gland": "Adrenal",
    "bladder": "Bladder",
    "colon": "Colon",
    "duodenum": "Duodenum",
    "esophagus": "Esophagus",
    "gallbladder": "Gallbladder",
    "kidney": "Kidney",
    "liver": "Liver",
    "pancreatic": "Pancreas",
    "prostate": "Prostate",
    "spleen": "Spleen",
    "stomach": "Stomach",
    "uterus": "Uterus",
}

MAX_REGIONS = 2
SUMMARY_MAX_PLACES = 3


def _label_font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _clip_box(box, w: int, h: int) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = box
    x1, x2 = sorted((int(round(max(0, min(w, x1)))), int(round(max(0, min(w, x2))))))
    y1, y2 = sorted((int(round(max(0, min(h, y1)))), int(round(max(0, min(h, y2))))))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def merge_regions(
    boxes: list[tuple[tuple[int, int, int, int], float]],
) -> list[tuple[tuple[int, int, int, int], float]]:
    """겹치는 박스를 감싸는 박스 하나로 합친다. 점수 높은 MAX_REGIONS 곳만."""
    groups: list[list] = []
    for box, conf in boxes:
        hit = [g for g in groups if any(_overlaps(box, b) for b, _ in g)]
        merged = [(box, conf)] + [item for g in hit for item in g]
        groups = [g for g in groups if all(g is not h for h in hit)] + [merged]
    regions = []
    for g in groups:
        xs1, ys1, xs2, ys2 = zip(*(b for b, _ in g))
        regions.append(((min(xs1), min(ys1), max(xs2), max(ys2)), max(c for _, c in g)))
    regions.sort(key=lambda r: r[1], reverse=True)
    return regions[:MAX_REGIONS]


def locate_abnormality(
    detections: list[Detection],
    img_uint8: np.ndarray,
) -> tuple[np.ndarray, dict]:
    """
    박스 → 마스크.

    Returns:
        mask_union, per_class {장기: {"prob", "mask", "boxes"}}
    """
    h, w = img_uint8.shape[:2]
    raw: dict[str, list] = {}
    for det in detections:
        box = _clip_box(det.box, w, h)
        if box is not None:
            raw.setdefault(det.finding, []).append((box, det.confidence))

    per_class: dict = {}
    mask_union = np.zeros((h, w), dtype=bool)
    for finding, boxes in raw.items():
        info = {
            "prob": 0.0,
            "mask": np.zeros((h, w), dtype=bool),
            "boxes": merge_regions(boxes),
        }
        for (x1, y1, x2, y2), conf in info["boxes"]:
            info["mask"][y1:y2, x1:x2] = True
            info["prob"] = max(info["prob"], conf)
        mask_union |= info["mask"]
        per_class[finding] = info
    return mask_union, per_class


def build_overlay_image(img_uint8: np.ndarray, per_class: dict) -> np.ndarray:
    """박스 안을 장기 색으로 옅게 칠하고 테두리·라벨을 그린다. outline_only 는 테두리만."""
    base = img_uint8.astype(np.float32) / 255.0
    overlay = base.copy()
    for name, info in per_class.items():
        if info.get("outline_only"):
            continue
        color = CLASS_COLORS.get(name, (1.0, 0.0, 0.0))
        m = info.get("mask")
        if m is None:
            continue
        for c in range(3):
            overlay[m, c] = overlay[m, c] * 0.65 + color[c] * 0.35
    width = max(2, round(min(img_uint8.shape[:2]) / 300))
    img = Image.fromarray((np.clip(overlay, 0, 1) * 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    font = _label_font(max(12, round(min(img.size) / 40)))
    for name, info in per_class.items():
        rgb = tuple(int(v * 255) for v in CLASS_COLORS.get(name, (1.0, 0.0, 0.0)))
        label = None if name in ("answer", "ai_ref") else BOX_LABELS.get(name, name)
        for (x1, y1, x2, y2), conf in info.get("boxes") or []:
            draw.rectangle([x1, y1, x2 - 1, y2 - 1], outline=rgb, width=width)
            if label and conf is not None:
                text = f"{label} {conf:.2f}"
                left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
                tw, th = right - left, bottom - top
                ty = y1 - th - 6 if y1 - th - 6 >= 0 else y1 + 2
                draw.rectangle([x1, ty, x1 + tw + 8, ty + th + 6], fill=rgb)
                draw.text((x1 + 4, ty + 2 - top), text, fill=(0, 0, 0), font=font)
    return np.asarray(img)


def overlay_to_base64(overlay: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(overlay).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _place(cy: float, cx: float, h: int, w: int) -> str:
    vert = "상부" if cy < h / 3 else ("하부" if cy > 2 * h / 3 else "중부")
    side = "우측" if cx < w / 3 else ("좌측" if cx > 2 * w / 3 else "중앙")
    return f"{vert}-{side}"


def summarize_targets(per_class: dict, *, classes: list[str]) -> str:
    """UI용 한 줄 요약 (예: '간: 상부-우측 0.72, 신장: 중부-좌측 0.55')."""
    bits = []
    for name in classes:
        info = per_class.get(name)
        if not info or not info["boxes"]:
            continue
        h, w = info["mask"].shape
        places: dict[str, list[float]] = {}
        for (x1, y1, x2, y2), conf in sorted(info["boxes"], key=lambda b: b[1], reverse=True):
            places.setdefault(_place((y1 + y2) / 2, (x1 + x2) / 2, h, w), []).append(conf)
        parts = []
        for place, confs in list(places.items())[:SUMMARY_MAX_PLACES]:
            count = f" (박스 {len(confs)}개)" if len(confs) > 1 else ""
            parts.append(f"{place} {confs[0]:.2f}{count}")
        if len(places) > SUMMARY_MAX_PLACES:
            parts.append(f"외 {len(places) - SUMMARY_MAX_PLACES}곳")
        bits.append(f"{class_ko(name)}: " + ", ".join(parts))
    return "; ".join(bits) if bits else "강조된 장기 없음"


class Localization:
    """박스 기반 장기 위치 표시 클래스."""

    locate_abnormality = staticmethod(locate_abnormality)
    merge_regions = staticmethod(merge_regions)
    build_overlay_image = staticmethod(build_overlay_image)
    overlay_to_base64 = staticmethod(overlay_to_base64)
    summarize_targets = staticmethod(summarize_targets)
