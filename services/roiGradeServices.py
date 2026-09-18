"""
학습 ROI 채점.

업로드 이미지에서 이상 부위를 찾고, 사용자가 그린 박스/원과
겹치는 정도로 정답 / 부분정답 / 오답을 판정한다.
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
# 오답 노트용 사용자 ROI 윤곽 (1px dashed cyan)
_ROI_OUTLINE = (0, 230, 255, 255)
_ROI_DASH = 4
_ROI_GAP = 3

_brain_mod: ModuleType | None = None


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
) -> dict:
    """이미지 + 사용자 ROI를 채점하고 overlay PNG(base64)를 돌려준다."""
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
        brain.ensemble_service, img_uint8, probs
    )

    scored = grade_overlap(user, mask_union)

    overlay_b64 = None
    overlay_summary = None
    overlay = None
    if scored["has_abnormality"]:
        overlay = brain.Localization.build_overlay_image(img_uint8, per_class)
        overlay_summary = brain.Localization.summarize_targets(per_class, targets)
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
        },
        "disclaimer": constants.DISCLAIMER,
        "_review_png": _to_png_bytes(review_img),
    }
