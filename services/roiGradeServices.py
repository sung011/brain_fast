"""
학습 ROI 채점.

업로드 이미지에서 이상 부위를 찾고, 사용자가 그린 박스/원과
겹치는 정도로 정답 / 부분정답 / 오답을 판정한다.
흉부 X-ray(st_part=2)는 class/thorax, 그 밖에는 class/brain 모델로 이상 부위를 찾는다.
"""
from __future__ import annotations

import io
import math
from importlib import import_module
from types import ModuleType

import numpy as np
from PIL import Image, ImageDraw

# 정답: 이상 부위를 충분히 덮음
CORRECT_IOU = 0.40
CORRECT_RECALL = 0.50
# 부분정답: 근처이거나 일부만 겹침
PARTIAL_IOU = 0.15
PARTIAL_RECALL = 0.25
# 중심이 이미지 대각선 대비 이 비율 이내면 "비슷함"
NEAR_CENTER_RATIO = 0.15
# 흉부: 의심 부위를 다 찾았어도 그린 영역 중 의심 부위가 이 비율보다 적으면 "너무 넓게 그림" → 부분정답
MIN_PRECISION = 0.25
# 오답 노트용 사용자 ROI 윤곽 (1px dashed cyan)
_ROI_OUTLINE = (0, 230, 255, 255)
_ROI_DASH = 4
_ROI_GAP = 3

# study.st_part / st_modal 코드 (schemas/studySchemas.py ST_PART_ALIASES)
THORAX_PART = "2"
XRAY_MODAL = "1"

_brain_mod: ModuleType | None = None
_thorax_mod: ModuleType | None = None


def load_brain() -> ModuleType:
    global _brain_mod
    if _brain_mod is not None:
        return _brain_mod
    try:
        _brain_mod = import_module("class.brain")
        return _brain_mod
    except Exception as exc:
        raise RuntimeError(
            f"뇌 분석 모듈을 불러오지 못했습니다. 의존성(torch 등)을 설치하세요. {exc}"
        ) from exc


def ensure_ensemble(brain: ModuleType) -> None:
    if brain.ensemble_service.ready:
        return
    try:
        brain.ensemble_service.load()
    except Exception as exc:
        raise RuntimeError(
            brain.ensemble_service.error or f"앙상블 모델 로드 실패: {exc}"
        ) from exc


def load_thorax() -> ModuleType:
    global _thorax_mod
    if _thorax_mod is not None:
        return _thorax_mod
    try:
        _thorax_mod = import_module("class.thorax")
        return _thorax_mod
    except Exception as exc:
        raise RuntimeError(
            f"흉부 분석 모듈을 불러오지 못했습니다. 의존성(torch 등)을 설치하세요. {exc}"
        ) from exc


def ensure_thorax(thorax: ModuleType) -> None:
    if thorax.detector_service.ready:
        return
    try:
        thorax.detector_service.load()
    except Exception as exc:
        raise RuntimeError(
            thorax.detector_service.error or f"흉부 모델 로드 실패: {exc}"
        ) from exc


def _to_px(value: float, size: int, normalized: bool) -> int:
    raw = float(value) * size if normalized else float(value)
    return int(round(raw))


def roi_mask(
    height: int,
    width: int,
    *,
    roi_type: str,
    normalized: bool,
    x: float | None = None,
    y: float | None = None,
    box_w: float | None = None,
    box_h: float | None = None,
    cx: float | None = None,
    cy: float | None = None,
    radius: float | None = None,
) -> np.ndarray:
    """사용자 ROI → 이미지 크기 bool 마스크."""
    kind = (roi_type or "").strip().lower()
    mask = np.zeros((height, width), dtype=bool)

    if kind in ("box", "rect", "rectangle"):
        if x is None or y is None or box_w is None or box_h is None:
            raise ValueError("박스는 x, y, width, height 가 필요합니다.")
        x1 = max(0, min(width, _to_px(x, width, normalized)))
        y1 = max(0, min(height, _to_px(y, height, normalized)))
        x2 = max(0, min(width, _to_px(x + box_w, width, normalized)))
        y2 = max(0, min(height, _to_px(y + box_h, height, normalized)))
        if x2 < x1:
            x1, x2 = x2, x1
        if y2 < y1:
            y1, y2 = y2, y1
        if x2 <= x1 or y2 <= y1:
            raise ValueError("박스 크기가 올바르지 않습니다.")
        mask[y1:y2, x1:x2] = True
        return mask

    if kind in ("circle", "ellipse", "원"):
        if cx is None or cy is None or radius is None:
            raise ValueError("원은 cx, cy, radius 가 필요합니다.")
        px = _to_px(cx, width, normalized)
        py = _to_px(cy, height, normalized)
        pr = _to_px(radius, min(width, height), normalized)
        if pr <= 0:
            raise ValueError("원 반지름이 올바르지 않습니다.")
        yy, xx = np.ogrid[:height, :width]
        mask = (xx - px) ** 2 + (yy - py) ** 2 <= pr ** 2
        if not mask.any():
            raise ValueError("원이 이미지 밖에 있습니다.")
        return mask

    raise ValueError("roi_type은 box 또는 circle 이어야 합니다.")


def _bbox_from_mask(mask: np.ndarray) -> dict | None:
    if mask is None or not mask.any():
        return None
    ys, xs = np.where(mask)
    x1, x2 = int(xs.min()), int(xs.max())
    y1, y2 = int(ys.min()), int(ys.max())
    h, w = mask.shape
    return {
        "x": round(x1 / w, 6),
        "y": round(y1 / h, 6),
        "width": round((x2 - x1 + 1) / w, 6),
        "height": round((y2 - y1 + 1) / h, 6),
        "x_px": x1,
        "y_px": y1,
        "width_px": x2 - x1 + 1,
        "height_px": y2 - y1 + 1,
    }


def _center(mask: np.ndarray) -> tuple[float, float] | None:
    if mask is None or not mask.any():
        return None
    ys, xs = np.where(mask)
    return float(np.mean(xs)), float(np.mean(ys))


def _dashed_polyline(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    color: tuple[int, int, int, int],
    *,
    dash: int = _ROI_DASH,
    gap: int = _ROI_GAP,
) -> None:
    """점들을 잇는 1px dashed 선."""
    if len(points) < 2:
        return
    drawing = True
    remaining = float(dash)
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        ux, uy = dx / length, dy / length
        pos = 0.0
        while pos < length:
            if remaining <= 0:
                drawing = not drawing
                remaining = float(dash if drawing else gap)
            take = min(remaining, length - pos)
            if drawing:
                sx = x1 + ux * pos
                sy = y1 + uy * pos
                ex = x1 + ux * (pos + take)
                ey = y1 + uy * (pos + take)
                draw.line([(sx, sy), (ex, ey)], fill=color, width=1)
            pos += take
            remaining -= take


def _ellipse_points(x1: int, y1: int, x2: int, y2: int) -> list[tuple[float, float]]:
    rx = (x2 - x1) / 2
    ry = (y2 - y1) / 2
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    n = max(64, int(2 * math.pi * max(abs(rx), abs(ry))))
    return [
        (cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
        for i in range(n + 1)
    ]


def _to_png_bytes(img_uint8: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img_uint8).save(buf, format="PNG")
    return buf.getvalue()


def draw_user_roi(img_uint8: np.ndarray, user: np.ndarray, roi_type: str) -> np.ndarray:
    """원본(또는 이상 부위 overlay) 위에 1px dashed ROI를 그린다."""
    if user is None or not user.any():
        return img_uint8
    ys, xs = np.where(user)
    x1, x2 = int(xs.min()), int(xs.max())
    y1, y2 = int(ys.min()), int(ys.max())
    kind = (roi_type or "").strip().lower()

    base = Image.fromarray(img_uint8).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    if kind in ("circle", "ellipse", "원"):
        points = _ellipse_points(x1, y1, x2, y2)
    else:
        points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
    _dashed_polyline(draw, points, _ROI_OUTLINE)
    return np.array(Image.alpha_composite(base, layer).convert("RGB"))


def grade_overlap(user: np.ndarray, gt: np.ndarray) -> dict:
    """IoU·재현율·중심 거리로 정답/부분정답/오답 판정."""
    inter = int(np.logical_and(user, gt).sum())
    union = int(np.logical_or(user, gt).sum())
    gt_n = int(gt.sum())
    user_n = int(user.sum())
    iou = (inter / union) if union else 0.0
    recall = (inter / gt_n) if gt_n else 0.0
    precision = (inter / user_n) if user_n else 0.0

    near = False
    center_dist = None
    h, w = user.shape
    uc = _center(user)
    gc = _center(gt)
    if uc and gc:
        center_dist = float(np.hypot(uc[0] - gc[0], uc[1] - gc[1]))
        diag = float(np.hypot(w, h))
        near = diag > 0 and (center_dist / diag) <= NEAR_CENTER_RATIO

    has_abnormality = gt_n > 0
    if not has_abnormality:
        result = "오답"
    elif iou >= CORRECT_IOU or recall >= CORRECT_RECALL:
        result = "정답"
    elif iou >= PARTIAL_IOU or recall >= PARTIAL_RECALL or near:
        result = "부분정답"
    else:
        result = "오답"

    return {
        "result": result,
        "has_abnormality": has_abnormality,
        "iou": round(iou, 4),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "near_center": near,
        "center_distance_px": round(center_dist, 2) if center_dist is not None else None,
    }


def _mask_box(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """마스크를 감싸는 박스 (x1, y1, x2, y2). 비어 있으면 None."""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _box_iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def _box_area(b) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _direction(user_box, target_box, width: int, height: int) -> str:
    """정답 부위가 그린 곳보다 어느 쪽에 있는지 (화면 기준). 거의 같으면 빈 문자열."""
    ux, uy = (user_box[0] + user_box[2]) / 2 / width, (user_box[1] + user_box[3]) / 2 / height
    tx, ty = (target_box[0] + target_box[2]) / 2 / width, (target_box[1] + target_box[3]) / 2 / height
    parts = []
    if ty - uy > 0.08:
        parts.append("더 아래쪽")
    elif uy - ty > 0.08:
        parts.append("더 위쪽")
    if tx - ux > 0.08:
        parts.append("화면 오른쪽")
    elif ux - tx > 0.08:
        parts.append("화면 왼쪽")
    return "·".join(parts)


def roi_feedback(
    user_masks: list[np.ndarray],
    target_boxes: list[tuple[int, int, int, int]],
    width: int,
    height: int,
    *,
    found: list[bool] | None = None,
    too_wide: bool = False,
    chest: bool = False,
) -> list[str]:
    """
    틀린 이유를 학습자가 알아듣게 한두 줄로 알려 준다. 채점 숫자(겹침·중심)를 말로 바꾼 것이다.
    위치는 분명하게, 크기는 너그럽게 말한다. 의사들도 "어디쯤"은 대체로 같지만 "얼마나 크게"는 사람마다 달라서다.
    학습자가 왜 틀렸는지(좌우 헷갈림 등)는 짐작하지 않고, 보이는 사실만 쉬운 말로 쓴다.

    - 정답 부위마다 가장 가까운 ROI 와 비교해: 반대쪽 폐(흉부) / 크기만 다름 / 거의 맞음(방향) / 다른 곳
    - 정답 부위와 전혀 겹치지 않는 ROI 가 있으면 알려 준다
    - 다 찾았지만 너무 넓게 그렸으면 알려 준다
    """
    users = [b for b in (_mask_box(m) for m in user_masks) if b]
    if not target_boxes:
        return []
    if not users:
        return ["ROI 를 그리지 않았습니다."]
    found = found or [False] * len(target_boxes)
    tips: list[str] = []
    for t, ok in zip(target_boxes, found):
        if ok:
            continue
        best = max(users, key=lambda u: (_box_iou(u, t), -math.hypot(
            (u[0] + u[2] - t[0] - t[2]) / 2, (u[1] + u[3] - t[1] - t[3]) / 2)))
        overlap = _box_iou(best, t)
        direction = _direction(best, t, width, height)
        ux = (best[0] + best[2]) / 2 / width
        tx = (t[0] + t[2]) / 2 / width
        if chest and abs(tx - ux) > 0.25 and (ux - 0.5) * (tx - 0.5) < 0:
            tips.append("정답은 반대쪽 폐에 있어요. 색칠된 곳을 확인해 보세요.")
        elif overlap > 0 and _box_area(best) < _box_area(t) * 0.5:
            tips.append("위치는 맞았어요. 정답 표시보다 작게 그렸는데, 크기는 의사마다 차이가 있어요. "
                        "다만 너무 작아서 정답 기준에는 못 미쳤어요.")
        elif overlap > 0 and direction:
            tips.append(f"거의 맞았어요. 정답은 조금 더 {direction}에 있어요.")
        elif overlap > 0:
            tips.append("위치는 맞았어요. 정답 표시와 크기가 많이 다른데, 크기는 의사마다 차이가 있어요. "
                        "다만 겹친 부분이 적어서 정답 기준에는 못 미쳤어요.")
        elif direction:
            tips.append(f"다른 곳을 골랐어요. 정답은 {direction}에 있어요.")
        else:
            tips.append("다른 곳을 골랐어요. 색칠된 곳을 확인해 보세요.")
    extra = sum(1 for u in users if all(_box_iou(u, t) == 0 for t in target_boxes))
    if extra and len(users) > 1:
        tips.append(f"정답이 아닌 곳에 그린 ROI 가 {extra}개 있어요.")
    if too_wide:
        tips.append("병변은 다 감쌌어요. 다만 너무 넓게 그려서, 이상한 부분만 감싸 보세요.")
    # 같은 말이 여러 번 나오지 않게
    return list(dict.fromkeys(tips))


def _roi_args(shape: dict, normalized: bool) -> dict:
    """schemas.roiGradeSchemas.RoiShape 한 개 → roi_mask 인자."""
    return dict(
        roi_type=shape["roi_type"],
        normalized=normalized,
        x=shape.get("x"),
        y=shape.get("y"),
        box_w=shape.get("width"),
        box_h=shape.get("height"),
        cx=shape.get("cx"),
        cy=shape.get("cy"),
        radius=shape.get("radius"),
    )


def grade_regions(
    user_masks: list[np.ndarray],
    targets: list[dict],
    height: int,
    width: int,
) -> dict:
    """
    흉부: 의심 부위마다 따로 채점한다.

    - 의심 부위 하나는 학습자 ROI 중 하나가 충분히 덮으면(IoU·재현율 기준) "찾음"
    - 모두 찾음 → 정답, 일부만 찾았거나 근처 → 부분정답, 아니면 오답
    - 다 찾았어도 그린 영역이 의심 부위보다 훨씬 넓으면(MIN_PRECISION 미만) 부분정답

    의심 부위를 하나로 합쳐 비교하지 않는 이유: 두 곳의 중심을 합치면 그 사이(아무것도 없는 곳)가
    "가깝다"고 판정되고, 한 곳만 찾았는지 둘 다 찾았는지 구분하지 못한다.
    """
    diag = float(np.hypot(width, height))
    user_any = np.zeros((height, width), dtype=bool)
    for mask in user_masks:
        user_any |= mask
    target_any = np.zeros((height, width), dtype=bool)

    graded = []
    for target in targets:
        x1, y1, x2, y2 = target["box"]
        t_mask = np.zeros((height, width), dtype=bool)
        t_mask[y1:y2, x1:x2] = True
        target_any |= t_mask
        t_n = int(t_mask.sum())
        center = ((x1 + x2) / 2, (y1 + y2) / 2)
        best_recall = best_iou = 0.0
        close = False
        for mask in user_masks:
            inter = int(np.logical_and(mask, t_mask).sum())
            union = int(np.logical_or(mask, t_mask).sum())
            best_recall = max(best_recall, inter / t_n if t_n else 0.0)
            best_iou = max(best_iou, inter / union if union else 0.0)
            uc = _center(mask)
            if uc and diag > 0 and math.hypot(uc[0] - center[0], uc[1] - center[1]) / diag <= NEAR_CENTER_RATIO:
                close = True
        found = best_iou >= CORRECT_IOU or best_recall >= CORRECT_RECALL
        near = found or close or best_iou >= PARTIAL_IOU or best_recall >= PARTIAL_RECALL
        graded.append({
            "x": round(x1 / width, 6),
            "y": round(y1 / height, 6),
            "width": round((x2 - x1) / width, 6),
            "height": round((y2 - y1) / height, 6),
            "findings": target["findings"],
            "confidence": None if target["confidence"] is None else round(float(target["confidence"]), 4),
            "found": found,
            "near": near,
            "recall": round(best_recall, 4),
            "iou": round(best_iou, 4),
        })

    user_n = int(user_any.sum())
    precision = int(np.logical_and(user_any, target_any).sum()) / user_n if user_n else 0.0
    found_count = sum(t["found"] for t in graded)
    if not graded:
        result = "오답"
    elif found_count == len(graded):
        result = "정답"
    elif found_count or any(t["near"] for t in graded):
        result = "부분정답"
    else:
        result = "오답"
    too_wide = result == "정답" and precision < MIN_PRECISION
    if too_wide:
        result = "부분정답"
    return {
        "result": result,
        "near_center": any(t["near"] for t in graded),
        "targets": graded,
        "target_count": len(graded),
        "found_count": found_count,
        "too_wide": too_wide,
    }


def _answer_targets(answer_boxes: list[dict], height: int, width: int) -> list[dict]:
    """정답 박스(0~1 비율 x, y, width, height, finding) → 의심 부위 목록. 데이터셋 정답이라 신뢰도는 없다."""
    targets = []
    for b in answer_boxes:
        x1 = max(0, min(width, round(float(b["x"]) * width)))
        y1 = max(0, min(height, round(float(b["y"]) * height)))
        x2 = max(0, min(width, round((float(b["x"]) + float(b["width"])) * width)))
        y2 = max(0, min(height, round((float(b["y"]) + float(b["height"])) * height)))
        if x2 > x1 and y2 > y1:
            targets.append({"box": (x1, y1, x2, y2), "confidence": None,
                            "findings": [b.get("finding") or "정답 부위"]})
    return targets


def _boxes_overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _thorax_targets(per_class: dict, constants: ModuleType) -> list[dict]:
    """
    소견별 박스 → 의심 부위 목록. 다른 소견이라도 박스가 겹치면 한 부위로 본다
    (예: 같은 자리의 폐음영·흉막삼출은 학습자에게 한 곳이다).
    """
    groups: list[list] = []
    for finding, info in per_class.items():
        for box, conf in info["boxes"]:
            hit = [g for g in groups if any(_boxes_overlap(box, b) for b, _, _ in g)]
            merged = [(box, conf, finding)] + [item for g in hit for item in g]
            groups = [g for g in groups if all(g is not h for h in hit)] + [merged]
    targets = []
    for g in groups:
        xs1, ys1, xs2, ys2 = zip(*(b for b, _, _ in g))
        targets.append({
            "box": (min(xs1), min(ys1), max(xs2), max(ys2)),
            "confidence": max(c for _, c, _ in g),
            "findings": [constants.class_ko(n) for n in sorted({f for _, _, f in g})],
        })
    targets.sort(key=lambda t: t["confidence"], reverse=True)
    return targets


def _metrics_text(recall, precision, source: str, comparable: bool) -> str:
    """
    맞은 정도를 숫자 한 줄로 쓴다. 판정(정답·부분정답·오답)은 건드리지 않고 숫자만 말한다.
    recall = 기준 부위 중 내 영역이 덮은 비율, precision = 내 영역 중 기준 부위 안에 있는 비율.
    그리지 않았거나 기준 부위가 없거나 값이 이상하면 빈 문자열.
    """
    if not comparable:
        return ""
    try:
        r, p = float(recall), float(precision)
    except (TypeError, ValueError):
        return ""
    if not (0.0 <= r <= 1.0 and 0.0 <= p <= 1.0):
        return ""
    base = "정답 부위" if source == "dataset" else "AI가 찾은 부위"
    return f"내 영역이 {base}의 {round(r * 100)}%를 덮었고, 내 영역 중 {round(p * 100)}%가 {base} 안에 있어요."


AI_REFERENCE_MAX = 3   # 참고로 보여 줄 AI 부위는 신뢰도 높은 순으로 이만큼만


def _ai_reference_targets(
    per_class: dict, height: int, width: int, constants: ModuleType, answer_findings: set[str] | None = None
) -> list[dict]:
    """
    데이터셋 정답이 기준일 때 화면에 참고로 덧그릴 AI 부위. 정답 소견과 같은 모델의 박스만 보여
    (흉막삼출 문제에 폐음영 박스가 같이 뜨지 않게) 겹치는 박스는 한 부위로 묶고,
    신뢰도 높은 순으로 AI_REFERENCE_MAX 곳만 둔다. 좌표는 0~1 비율(x, y, width, height)로도 준다.
    정답 소견 이름이 어느 모델과도 맞지 않으면 모든 모델의 박스를 쓴다.
    """
    if answer_findings:
        same = {code: info for code, info in per_class.items() if constants.class_ko(code) in answer_findings}
        per_class = same or per_class
    out = []
    for t in _thorax_targets(per_class, constants)[:AI_REFERENCE_MAX]:
        x1, y1, x2, y2 = t["box"]
        out.append({
            "box": t["box"],
            "x": x1 / width, "y": y1 / height,
            "width": (x2 - x1) / width, "height": (y2 - y1) / height,
            "confidence": float(t["confidence"]),
            "findings": t["findings"],
        })
    return out


def grade_image_roi(
    image_bytes: bytes,
    filename: str,
    *,
    roi_type: str,
    normalized: bool = True,
    x: float | None = None,
    y: float | None = None,
    width: float | None = None,
    height: float | None = None,
    cx: float | None = None,
    cy: float | None = None,
    radius: float | None = None,
    include_overlay: bool = True,
    st_part: str | None = None,
    st_modal: str | None = None,
    rois: list[dict] | None = None,
    answer_boxes: list[dict] | None = None,
) -> dict:
    """
    이미지 + 사용자 ROI를 채점하고 overlay PNG(base64)를 돌려준다.

    st_part 가 흉부(2)면 흉부 X-ray 모델로, 그 밖에는 뇌 CT 모델로 채점한다.
    rois(ROI 여러 개)는 흉부만 쓴다. 없으면 roi_type·좌표 한 개로 채점한다. 뇌는 rois 를 보지 않는다.
    answer_boxes(데이터셋 정답 박스)도 흉부만 쓴다. 있으면 AI 대신 그 박스로 채점하고 AI 결과는 참고로만 둔다.
    """
    if st_part == THORAX_PART:
        if st_modal not in (None, XRAY_MODAL):
            raise ValueError("흉부는 X-ray 영상만 채점할 수 있습니다.")
        single = dict(roi_type=roi_type, x=x, y=y, width=width, height=height, cx=cx, cy=cy, radius=radius)
        return _grade_thorax(
            image_bytes,
            filename,
            roi_args_list=[_roi_args(shape, normalized) for shape in (rois or [single])],
            include_overlay=include_overlay,
            answer_boxes=answer_boxes,
        )

    config = import_module("class.brain.config")
    constants = import_module("class.brain.constants")

    if len(image_bytes) > config.MAX_UPLOAD_BYTES:
        raise ValueError("이미지가 너무 큽니다 (최대 5MB).")
    if len(image_bytes) < 64:
        raise ValueError("유효한 이미지가 아닙니다.")

    brain = load_brain()
    ensure_ensemble(brain)

    try:
        img_uint8 = brain.Preprocess.load_image_uint8_from_bytes(
            image_bytes, filename or "roi.png"
        )
    except Exception as exc:
        raise ValueError(f"이미지 디코딩 실패: {exc}") from exc

    h, w = img_uint8.shape[:2]
    user = roi_mask(
        h,
        w,
        roi_type=roi_type,
        normalized=normalized,
        x=x,
        y=y,
        box_w=width,
        box_h=height,
        cx=cx,
        cy=cy,
        radius=radius,
    )

    probs = brain.ensemble_service.predict(img_uint8)
    _, mask_union, per_class, targets = brain.Localization.locate_abnormality(
        brain.ensemble_service.hemorrhage,
        img_uint8,
        probs,
        classes=constants.CLASSES,
        task="hemorrhage",
    )

    germinoma_cls = None
    if brain.ensemble_service.germinoma and brain.ensemble_service.germinoma.ready:
        probs_g = brain.ensemble_service.germinoma.predict(img_uint8)
        g_classes = list(brain.ensemble_service.germinoma.classes)
        _, mask_g, per_g, targets_g = brain.Localization.locate_abnormality(
            brain.ensemble_service.germinoma,
            img_uint8,
            probs_g,
            classes=g_classes,
            task="germinoma",
        )
        if float(np.max(probs_g)) >= 0.45:
            mask_union = mask_union | mask_g
            per_class = {**per_class, **per_g}
        g_findings = [
            {
                "label": name,
                "label_ko": constants.class_ko(name),
                "score": float(probs_g[i]),
            }
            for i, name in enumerate(g_classes)
        ]
        g_findings.sort(key=lambda f: f["score"], reverse=True)
        g_top = g_findings[0]
        germinoma_cls = {
            "top_label": g_top["label"],
            "top_label_ko": g_top["label_ko"],
            "confidence": g_top["score"],
            "findings": g_findings,
            "probs": [float(probs_g[i]) for i in range(len(g_classes))],
            "task": "germinoma",
        }

    scored = grade_overlap(user, mask_union)
    gt_box = _mask_box(mask_union)
    scored["feedback"] = (
        [] if scored["result"] == "정답" or gt_box is None
        else roi_feedback([user], [gt_box], w, h, found=[False])
    )

    overlay_b64 = None
    overlay_summary = None
    overlay = None
    if scored["has_abnormality"]:
        overlay = brain.Localization.build_overlay_image(img_uint8, per_class)
        overlay_summary = brain.Localization.summarize_targets(
            per_class, targets, classes=constants.CLASSES
        )
        if germinoma_cls and float(germinoma_cls["confidence"]) >= 0.45:
            g_sum = brain.Localization.summarize_targets(
                per_class,
                [0],
                classes=[germinoma_cls["top_label"]],
            )
            if g_sum and g_sum != "강조된 이상 부위 없음":
                overlay_summary = (
                    f"{overlay_summary} | {g_sum}"
                    if overlay_summary and overlay_summary != "강조된 이상 부위 없음"
                    else g_sum
                )
        if include_overlay:
            overlay_b64 = brain.Localization.overlay_to_base64(overlay)

    review_img = draw_user_roi(
        overlay if overlay is not None else img_uint8,
        user,
        roi_type,
    )

    findings = [
        {
            "label": name,
            "label_ko": constants.class_ko(name),
            "score": float(probs[i]),
        }
        for i, name in enumerate(constants.CLASSES)
    ]
    findings.sort(key=lambda f: f["score"], reverse=True)
    top = findings[0]

    return {
        "ok": True,
        "result": scored["result"],
        "has_abnormality": scored["has_abnormality"],
        "iou": scored["iou"],
        "recall": scored["recall"],
        "precision": scored["precision"],
        "near_center": scored["near_center"],
        "feedback": scored["feedback"],
        "center_distance_px": scored["center_distance_px"],
        "overlay_png_base64": overlay_b64,
        "overlay_summary": overlay_summary,
        "abnormality_bbox": _bbox_from_mask(mask_union),
        "user_roi_bbox": _bbox_from_mask(user),
        "image": {"width": w, "height": h},
        "classification": {
            "top_label": top["label"],
            "top_label_ko": top["label_ko"],
            "confidence": top["score"],
            "findings": findings,
            "task": "hemorrhage",
        },
        "germinoma": germinoma_cls,
        "disclaimer": constants.DISCLAIMER,
        "_review_png": _to_png_bytes(review_img),
    }


def _grade_thorax(
    image_bytes: bytes,
    filename: str,
    *,
    roi_args_list: list[dict],
    include_overlay: bool,
    answer_boxes: list[dict] | None = None,
) -> dict:
    """
    흉부 X-ray 채점. 응답 형식은 뇌 CT 채점과 같고(germinoma 는 항상 None),
    의심 부위별 결과(targets, target_count, found_count, too_wide)가 더 붙는다.

    이상 부위는 소견 탐지 모델이 찾은 박스들이다. 박스가 하나도 없으면 오답으로 처리된다.
    학습자 ROI 는 여러 개일 수 있고, 판정은 grade_regions 가 의심 부위마다 한다.

    answer_boxes(데이터셋 정답 박스)가 있으면 의심 부위는 그 박스다(target_source="dataset").
    이때 overlay 는 정답 박스를 그리고, AI 가 찾은 부위는 overlay_summary 에 "AI 참고"로만 적는다.
    """
    config = import_module("class.thorax.config")
    constants = import_module("class.thorax.constants")

    if len(image_bytes) > config.MAX_UPLOAD_BYTES:
        raise ValueError("이미지가 너무 큽니다 (최대 5MB).")
    if len(image_bytes) < 64:
        raise ValueError("유효한 이미지가 아닙니다.")

    thorax = load_thorax()
    ensure_thorax(thorax)
    service = thorax.detector_service

    try:
        img_uint8 = thorax.Preprocess.load_image_uint8_from_bytes(
            image_bytes, filename or "roi.png"
        )
    except Exception as exc:
        raise ValueError(f"이미지 디코딩 실패: {exc}") from exc

    h, w = img_uint8.shape[:2]
    users = [(roi_mask(h, w, **args), args["roi_type"]) for args in roi_args_list]
    user = np.zeros((h, w), dtype=bool)
    for mask, _ in users:
        user |= mask

    classes = list(service.classes)
    detections = service.detect(img_uint8)
    probs = service.scores(detections)
    mask_union, per_class = thorax.Localization.locate_abnormality(detections, img_uint8)
    ai_summary = thorax.Localization.summarize_targets(per_class, classes=classes)

    if answer_boxes:
        source = "dataset"
        targets = _answer_targets(answer_boxes, h, w)
        answer_mask = np.zeros((h, w), dtype=bool)
        for x1, y1, x2, y2 in (t["box"] for t in targets):
            answer_mask[y1:y2, x1:x2] = True
    else:
        source = "ai"
        targets = _thorax_targets(per_class, constants)
        answer_mask = mask_union
    # overlay 는 채점에 쓴 의심 부위만 한 색으로 그린다 (화면 범례와 같은 색. 데이터셋 정답이면 AI 박스와 섞지 않는다)
    shown = {"answer": {"mask": answer_mask, "boxes": [(t["box"], 1.0) for t in targets]}}
    # 데이터셋 정답이 기준이면 AI 가 찾은 부위도 참고로 파란 테두리만 덧그린다(정답 위에 오지 않게 먼저 둔다)
    answer_findings = {str(b.get("finding")) for b in (answer_boxes or []) if b.get("finding")}
    ai_targets = _ai_reference_targets(per_class, h, w, constants, answer_findings) if source == "dataset" else []
    if ai_targets:
        shown = {
            "ai_ref": {"mask": mask_union, "outline_only": True,
                       "boxes": [(t["box"], t["confidence"]) for t in ai_targets]},
            **shown,
        }

    # iou·recall·precision 은 전체를 합쳐 계산한 참고값. 판정(result)은 의심 부위별 채점으로 바꾼다
    scored = grade_overlap(user, answer_mask)
    scored.update(grade_regions([m for m, _ in users], targets, h, w))
    scored["feedback"] = roi_feedback(
        [m for m, _ in users], [t["box"] for t in targets], w, h,
        found=[t["found"] for t in scored["targets"]], too_wide=scored["too_wide"], chest=True,
    )

    overlay_b64 = None
    overlay_summary = None
    overlay = None
    if scored["has_abnormality"]:
        overlay = thorax.Localization.build_overlay_image(img_uint8, shown)
        overlay_summary = ai_summary if source == "ai" else f"AI 참고 — {ai_summary}"
        if include_overlay:
            overlay_b64 = thorax.Localization.overlay_to_base64(overlay)

    review_img = overlay if overlay is not None else img_uint8
    for mask, kind in users:
        review_img = draw_user_roi(review_img, mask, kind)

    findings = [
        {
            "label": name,
            "label_ko": constants.class_ko(name),
            "score": float(probs[i]),
        }
        for i, name in enumerate(classes)
    ]
    findings.sort(key=lambda f: f["score"], reverse=True)
    top = findings[0]

    return {
        "ok": True,
        "result": scored["result"],
        "has_abnormality": scored["has_abnormality"],
        "iou": scored["iou"],
        "recall": scored["recall"],
        "precision": scored["precision"],
        "near_center": scored["near_center"],
        "feedback": scored["feedback"],
        "center_distance_px": scored["center_distance_px"],
        "overlay_png_base64": overlay_b64,
        "overlay_summary": overlay_summary,
        "abnormality_bbox": _bbox_from_mask(answer_mask),
        "user_roi_bbox": _bbox_from_mask(user),
        "image": {"width": w, "height": h},
        # 채점 기준: dataset = 데이터셋 정답 박스, ai = 탐지 모델 결과
        "target_source": source,
        # 맞은 정도 숫자 한 줄(화면이 그대로 보여 준다). 그리지 않았거나 기준 부위가 없으면 빈 문자열
        "metrics_text": _metrics_text(
            scored["recall"], scored["precision"], source, bool(user.any() and answer_mask.any())
        ),
        # 의심 부위별 결과 (흉부만). 0~1 비율 좌표, found=찾음, near=근처
        "targets": scored["targets"],
        "target_count": scored["target_count"],
        "found_count": scored["found_count"],
        "too_wide": scored["too_wide"],
        # 정답이 데이터셋 박스일 때 AI 가 찾은 부위(참고). 0~1 비율 좌표. 없으면 빈 목록
        "ai_targets": [{k: v for k, v in t.items() if k != "box"} for t in ai_targets],
        "classification": {
            "top_label": top["label"],
            "top_label_ko": top["label_ko"],
            "confidence": top["score"],
            "findings": findings,
            "task": "thorax",
        },
        "germinoma": None,
        "disclaimer": constants.DISCLAIMER,
        "_review_png": _to_png_bytes(review_img),
    }
