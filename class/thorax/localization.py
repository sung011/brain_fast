"""
흉부 X-ray 이상 부위 표시.

탐지 모델이 준 박스를 위치로 쓴다 (뇌 CT처럼 Grad-CAM으로 추정하지 않는다).
- 같은 소견에서 겹치는 박스는 하나로 합치고, 소견마다 점수 높은 MAX_REGIONS_PER_FINDING 곳만 남긴다
  (모델이 같은 자리에 박스를 여러 개 내서 화면이 어지러웠다)
- 소견별 마스크: 그 소견 박스들을 칠한 영역 → ROI 채점에 쓴다
- overlay: 박스 안을 소견 색으로 옅게 칠하고 테두리를 그린 PNG
"""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .constants import CLASS_COLORS, class_ko
from .ensemble import Detection

# 박스 옆에 붙이는 짧은 이름. 서버 글꼴에 한글이 없을 수 있어 영문으로 쓴다(채점 정답 박스는 라벨을 달지 않는다)
BOX_LABELS = {
    "airspace_opacity": "Opacity",
    "pleural_effusion": "Effusion",
    "pneumothorax": "Pneumothorax",
    "cardiomegaly": "Cardiomegaly",
    "consolidation": "Consolidation",
    "ai_ref": "AI",
}


def _label_font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:    # 오래된 Pillow 는 크기를 못 준다
        return ImageFont.load_default()


def _clip_box(box, w: int, h: int) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = box
    x1, x2 = sorted((int(round(max(0, min(w, x1)))), int(round(max(0, min(w, x2))))))
    y1, y2 = sorted((int(round(max(0, min(h, y1)))), int(round(max(0, min(h, y2))))))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


MAX_REGIONS_PER_FINDING = 2   # 양쪽 폐에 하나씩 있을 수 있어 1이 아니라 2


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def merge_regions(boxes: list[tuple[tuple[int, int, int, int], float]]) -> list[tuple[tuple[int, int, int, int], float]]:
    """
    겹치는 박스를 감싸는 박스 하나로 합친다 (겹침이 이어지면 모두 한 덩어리). 점수는 가장 높은 값.
    점수 높은 순으로 MAX_REGIONS_PER_FINDING 곳만 돌려준다.

    가장 높은 박스 하나만 남기지 않는 이유: 위양성 박스가 진짜 병변보다 점수가 높을 수 있다.
    """
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
    return regions[:MAX_REGIONS_PER_FINDING]


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
    raw: dict[str, list] = {}
    for det in detections:
        box = _clip_box(det.box, w, h)
        if box is not None:
            raw.setdefault(det.finding, []).append((box, det.confidence))

    per_class: dict = {}
    mask_union = np.zeros((h, w), dtype=bool)
    # 채점 마스크도 화면에 보이는 정리된 영역으로 만든다 (안 보이는 박스로 정답이 되지 않게)
    for finding, boxes in raw.items():
        info = {"prob": 0.0, "mask": np.zeros((h, w), dtype=bool), "boxes": merge_regions(boxes)}
        for (x1, y1, x2, y2), conf in info["boxes"]:
            info["mask"][y1:y2, x1:x2] = True
            info["prob"] = max(info["prob"], conf)
        mask_union |= info["mask"]
        per_class[finding] = info
    return mask_union, per_class


def build_overlay_image(img_uint8: np.ndarray, per_class: dict) -> np.ndarray:
    """박스 안을 소견 색으로 옅게 칠하고 테두리를 그린다."""
    base = img_uint8.astype(np.float32) / 255.0
    overlay = base.copy()
    for name, info in per_class.items():
        if info.get("outline_only"):
            continue    # 참고용 박스는 칠하지 않고 테두리만 그린다
        color = CLASS_COLORS.get(name, (1.0, 0.0, 0.0))
        m = info["mask"]
        for c in range(3):
            overlay[m, c] = overlay[m, c] * 0.65 + color[c] * 0.35
    img = Image.fromarray((np.clip(overlay, 0, 1) * 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    width = max(2, round(min(img.size) / 300))
    font = _label_font(max(12, round(min(img.size) / 40)))
    for name, info in per_class.items():
        rgb = tuple(int(v * 255) for v in CLASS_COLORS.get(name, (1.0, 0.0, 0.0)))
        label = BOX_LABELS.get(name)
        for (x1, y1, x2, y2), conf in info["boxes"]:
            draw.rectangle([x1, y1, x2 - 1, y2 - 1], outline=rgb, width=width)
            if label:    # 어느 박스가 무슨 소견이고 점수가 얼마인지 박스 위에 적는다
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


def summarize_targets(per_class: dict, *, classes: list[str]) -> str:
    """
    UI용 한 줄 요약 (예: '흉막삼출: 하부-환자 우측 0.28, 중부-환자 좌측 0.08').

    흉부 PA 영상은 화면 왼쪽이 환자의 오른쪽이다. 좌우는 환자 기준으로 적는다.
    박스마다 위치와 신뢰도를 적는다(신뢰도 순, 같은 위치는 묶고 최대 SUMMARY_MAX_PLACES 곳).
    박스 하나만 고르면 위양성 박스가 진짜 병변보다 점수가 높을 때 위치를 잘못 알려 준다.
    """
    bits = []
    for name in classes:
        info = per_class.get(name)
        if not info or not info["boxes"]:
            continue
        h, w = info["mask"].shape
        # 같은 위치의 박스는 하나로 묶고 가장 높은 신뢰도와 개수를 적는다
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
    return "; ".join(bits) if bits else "강조된 이상 부위 없음"


SUMMARY_MAX_PLACES = 3


def _place(cy: float, cx: float, h: int, w: int) -> str:
    """위아래는 3등분, 좌우는 가운데 20%(종격동 폭)만 중앙으로 본다."""
    vert = "상부" if cy < h / 3 else ("하부" if cy > 2 * h / 3 else "중부")
    side = "환자 우측" if cx < w * 0.4 else ("환자 좌측" if cx > w * 0.6 else "중앙")
    return f"{vert}-{side}"


def describe_box(x: float, y: float, width: float, height: float) -> str:
    """0~1 비율 박스 → "하부-환자 우측" 같은 위치 이름. 학습 해설의 "이 영상에서는"에 쓴다."""
    return _place(y + height / 2, x + width / 2, 1, 1)


class Localization:
    """박스 기반 이상 부위 표시 클래스."""

    locate_abnormality = staticmethod(locate_abnormality)
    merge_regions = staticmethod(merge_regions)
    build_overlay_image = staticmethod(build_overlay_image)
    overlay_to_base64 = staticmethod(overlay_to_base64)
    summarize_targets = staticmethod(summarize_targets)
    describe_box = staticmethod(describe_box)
