"""
흉부 X-ray 이상 부위 표시.

탐지 모델이 준 박스를 위치로 쓴다 (뇌 CT처럼 Grad-CAM으로 추정하지 않는다).
- 같은 소견에서 겹치는 박스는 하나로 합치고, 소견마다 점수 높은 MAX_REGIONS_PER_FINDING 곳만 남긴다
  (모델이 같은 자리에 박스를 여러 개 내서 화면이 어지러웠다)
- 폐음영처럼 경계가 흐린 소견(HEAT_FINDINGS)은 감싸 합치지 않고 '겹쳐 칠하기'를 쓴다.
  감싸 합치면 의사 박스보다 약 2배 커졌다(흉부 공통 시험 NIH 폐음영 230장, 2026-11-05 측정)
- 소견별 마스크: 화면에 칠한 영역 → ROI 채점에 쓴다
- overlay: 영역을 소견 색으로 옅게 칠하고 테두리(겹쳐 칠하기는 윤곽선)를 그린 PNG
"""
from __future__ import annotations

import base64
import io
import math

import cv2
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

# 박스 크기 보정 (가로 배율, 세로 배율, 늘어난 높이 중 위쪽으로 보내는 몫). 가로는 가운데를 두고 넓힌다.
# 심비대 모델은 VinDr 로 학습해 심장 아래쪽 폭만 담은 납작한 박스를 낸다. NIH·PadChest 의사 박스는 심장 전체를 감싸서
# 높이가 약 2배다(중앙값 NIH 2.02, PadChest 1.86). 위아래로 똑같이 늘리면 횡격막 아래로 삐져나가서 위로만 늘린다.
# 값은 NIH 로 고르고 PadChest-GR 로 확인했다(2026-11-05, 노트북 15 칸 48):
# 의사 박스와 절반 이상 겹친 사진 NIH 23% -> 80%, PadChest 32% -> 69%.
BOX_SCALE = {"cardiomegaly": (1.3, 1.8, 1.0)}


def _scale_box(box, kx: float = 1.0, ky: float = 1.0, up: float = 0.5):
    if kx == 1.0 and ky == 1.0:
        return box
    x1, y1, x2, y2 = box
    cx, hw, add = (x1 + x2) / 2, (x2 - x1) * kx / 2, (y2 - y1) * (ky - 1)
    return cx - hw, y1 - add * up, cx + hw, y2 + add * (1 - up)


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


# 겹쳐 칠하기 설정. 값은 흉부 공통 시험 NIH 폐음영으로 정했고 PadChest-GR 로 확인만 했다
HEAT_FINDINGS = {"airspace_opacity"}
HEAT_GRID = 256        # 계산용 격자: 긴 변을 256칸으로 줄여서 칠한다
HEAT_BLUR = 0.02       # 번지게 하는 정도 (격자 긴 변 대비)
HEAT_KEEP = 0.5        # 가장 진한 곳의 50% 이상만 영역으로 남긴다


def heat_regions(
    boxes: list[tuple[tuple[int, int, int, int], float]], w: int, h: int
) -> tuple[np.ndarray, list[tuple[tuple[int, int, int, int], float]]]:
    """
    박스를 신뢰도만큼 겹쳐 칠하고, 진한 곳만 남겨 영역을 만든다.

    여러 박스가 겹친 곳일수록 진해지므로, 감싸 합칠 때처럼 바깥 박스 때문에 영역이 커지지 않는다.
    영역마다 감싸는 박스와 그 안 박스의 최고 신뢰도를 돌려준다 (점수 높은 MAX_REGIONS_PER_FINDING 곳).

    Returns:
        mask (h, w) bool, [((x1, y1, x2, y2), conf), ...]
    """
    s = min(1.0, HEAT_GRID / max(w, h))    # 작은 이미지는 키우지 않는다(좌표가 반올림으로 밀리지 않게)
    gw, gh = max(1, math.ceil(w * s)), max(1, math.ceil(h * s))
    heat = np.zeros((gh, gw), dtype=np.float32)
    cells = []
    for (x1, y1, x2, y2), conf in boxes:
        c = (round(x1 * s), round(y1 * s), max(round(x2 * s), round(x1 * s) + 1), max(round(y2 * s), round(y1 * s) + 1))
        heat[c[1]:c[3], c[0]:c[2]] += conf
        cells.append((c, conf))
    if heat.max() <= 0:
        return np.zeros((h, w), dtype=bool), []
    heat = cv2.GaussianBlur(heat, (0, 0), HEAT_GRID * HEAT_BLUR)
    small = (heat >= HEAT_KEEP * heat.max()).astype(np.uint8)
    n, labels = cv2.connectedComponents(small, connectivity=8)
    found = []
    for i in range(1, n):
        part = labels == i
        conf = max((cf for (x1, y1, x2, y2), cf in cells if part[y1:y2, x1:x2].any()), default=0.0)
        found.append((conf, i))
    found.sort(reverse=True)
    keep = [i for _, i in found[:MAX_REGIONS_PER_FINDING]]
    big = cv2.resize(labels.astype(np.int32).astype(np.float32), (w, h), interpolation=cv2.INTER_NEAREST).astype(np.int32)
    mask = np.isin(big, keep)
    regions = []
    for conf, i in found[:MAX_REGIONS_PER_FINDING]:
        ys, xs = np.nonzero(big == i)
        if len(xs):
            regions.append(((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), float(conf)))
    return mask, regions


def shape_regions(
    shapes: list[tuple[list, float]], w: int, h: int
) -> tuple[np.ndarray, list[tuple[tuple[int, int, int, int], float]]]:
    """
    모양(segmentation) 모델이 준 병변 윤곽을 그대로 칠한다.

    이어진 덩어리마다 감싸는 박스와 그 안 윤곽의 최고 신뢰도를 돌려준다 (점수 높은 MAX_REGIONS_PER_FINDING 곳).
    기흉처럼 얇은 띠 모양 소견은 네모로 칠하면 대부분 정상 폐를 덮는다(SIIM 시험: 박스 3.2배 넓음, 모양 0.96배).
    """
    canvas = np.zeros((h, w), dtype=np.uint8)
    pieces = []
    for poly, conf in shapes:
        one = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(one, [np.round(np.asarray(poly, dtype=np.float32)).astype(np.int32)], 1)
        canvas |= one
        pieces.append((one.astype(bool), conf))
    n, labels = cv2.connectedComponents(canvas, connectivity=8)
    found = []
    for i in range(1, n):
        part = labels == i
        found.append((max((c for m, c in pieces if (m & part).any()), default=0.0), i))
    found.sort(reverse=True)
    mask = np.isin(labels, [i for _, i in found[:MAX_REGIONS_PER_FINDING]])
    regions = []
    for conf, i in found[:MAX_REGIONS_PER_FINDING]:
        ys, xs = np.nonzero(labels == i)
        regions.append(((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), float(conf)))
    return mask, regions


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
    shapes: dict[str, list] = {}
    for det in detections:
        if det.polygon:     # 모양 모델: 윤곽을 그대로 쓴다
            shapes.setdefault(det.finding, []).append((det.polygon, det.confidence))
            continue
        box = _clip_box(_scale_box(det.box, *BOX_SCALE.get(det.finding, ())), w, h)
        if box is not None:
            raw.setdefault(det.finding, []).append((box, det.confidence))

    per_class: dict = {}
    mask_union = np.zeros((h, w), dtype=bool)
    # 채점 마스크도 화면에 보이는 정리된 영역으로 만든다 (안 보이는 박스로 정답이 되지 않게)
    for finding, polys in shapes.items():
        mask, regions = shape_regions(polys, w, h)
        per_class[finding] = {"prob": max((c for _, c in regions), default=0.0), "mask": mask, "boxes": regions, "shape": True}
        mask_union |= mask
    for finding, boxes in raw.items():
        if finding in per_class:
            continue        # 같은 소견에 모양이 있으면 모양을 쓴다
        if finding in HEAT_FINDINGS:
            mask, regions = heat_regions(boxes, w, h)
            info = {"prob": max((c for _, c in regions), default=0.0), "mask": mask, "boxes": regions, "heat": True}
            mask_union |= info["mask"]
            per_class[finding] = info
            continue
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
    width = max(2, round(min(img_uint8.shape[:2]) / 300))
    for name, info in per_class.items():
        if (info.get("heat") or info.get("shape")) and not info.get("outline_only"):
            # 겹쳐 칠하기·모양 모델은 네모 대신 칠한 영역의 윤곽선을 그린다
            color = CLASS_COLORS.get(name, (1.0, 0.0, 0.0))
            m = info["mask"].astype(np.uint8)
            edge = (m - cv2.erode(m, np.ones((3, 3), np.uint8), iterations=width)).astype(bool)
            overlay[edge] = color
    img = Image.fromarray((np.clip(overlay, 0, 1) * 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    font = _label_font(max(12, round(min(img.size) / 40)))
    for name, info in per_class.items():
        rgb = tuple(int(v * 255) for v in CLASS_COLORS.get(name, (1.0, 0.0, 0.0)))
        label = BOX_LABELS.get(name)
        for (x1, y1, x2, y2), conf in info["boxes"]:
            if not (info.get("heat") or info.get("shape")):
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
