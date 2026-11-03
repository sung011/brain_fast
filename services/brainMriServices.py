"""
뇌 MRI 학습 채점.

검출 박스와 학습자가 그린 박스를 뇌 CT ROI 채점과 같은 겹침 규칙으로 비교하고,
제출 이미지를 review_node 에 남긴다.
"""

from __future__ import annotations

import logging

import numpy as np

from db import SessionLocal
from services import reviewNodeServices as review_node_service
from services.roiGradeServices import grade_overlap, grade_regions, roi_mask

_log = logging.getLogger("mediscan.brain_mri")


def _clip_box(box: list[float], height: int, width: int) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = (int(round(float(v))) for v in box)
    x1 = max(0, min(width, x1))
    x2 = max(0, min(width, x2))
    y1 = max(0, min(height, y1))
    y2 = max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _reference_box(item: dict, height: int, width: int) -> dict[str, float] | None:
    clipped = _clip_box(item["bbox"], height, width)
    if clipped is None or width <= 0 or height <= 0:
        return None
    x1, y1, x2, y2 = clipped
    return {
        "x": round(x1 / width, 6),
        "y": round(y1 / height, 6),
        "width": round((x2 - x1) / width, 6),
        "height": round((y2 - y1) / height, 6),
    }


def grade_box(
    height: int,
    width: int,
    predictions: list[dict],
    *,
    x: float,
    y: float,
    box_w: float,
    box_h: float,
) -> dict:
    """학습자 박스와 검출 박스를 비교해 정답/부분정답/오답을 만든다."""
    user = roi_mask(
        height,
        width,
        roi_type="box",
        normalized=True,
        x=x,
        y=y,
        box_w=box_w,
        box_h=box_h,
    )
    targets = []
    for item in predictions:
        clipped = _clip_box(item["bbox"], height, width)
        if clipped is None:
            continue
        targets.append(
            {
                "box": clipped,
                "confidence": item.get("confidence"),
                "findings": [item.get("disease_ko") or item.get("disease") or "검출"],
            }
        )
    findings = [
        {
            "label": item.get("disease"),
            "label_ko": item.get("disease_ko") or item.get("disease"),
            "score": item.get("confidence"),
        }
        for item in predictions
    ]
    top = max(predictions, key=lambda item: float(item.get("confidence") or 0)) if predictions else None
    classification = {
        "task": "brain-mri",
        "top_label": "" if top is None else top.get("disease") or "",
        "top_label_ko": "" if top is None else (top.get("disease_ko") or top.get("disease") or ""),
        "confidence": None if top is None else top.get("confidence"),
        "findings": findings,
    }
    if not targets:
        overlap = grade_overlap(user, np.zeros((height, width), dtype=bool))
        return {
            "result": overlap["result"],
            "iou": overlap["iou"],
            "recall": overlap["recall"],
            "reference_bbox": None,
            "overlay_summary": "검출된 이상 부위 없음",
            "classification": classification,
        }
    graded = grade_regions([user], targets, height, width)
    best = max(graded["targets"], key=lambda item: item["iou"])
    return {
        "result": graded["result"],
        "iou": best["iou"],
        "recall": best["recall"],
        "reference_bbox": None if top is None else _reference_box(top, height, width),
        "overlay_summary": f"검출 {len(targets)}곳 중 {graded['found_count']}곳을 표시",
        "classification": classification,
    }


async def save_grade(
    *,
    image_bytes: bytes,
    image_filename: str,
    grade: dict,
    study_idx: int | None,
    user_idx: int | None,
    slice_index: int | None,
) -> dict | None:
    """채점 결과를 review_node 에 남긴다. 저장이 실패하면 None."""
    db = SessionLocal()
    try:
        return await review_node_service.save_submission(
            db,
            image_bytes=image_bytes,
            image_filename=image_filename,
            grade=grade,
            study_idx=study_idx,
            user_idx=user_idx,
            slice_index=slice_index,
        )
    except Exception as exc:
        _log.warning("뇌 MRI 채점 저장 실패: %s", exc.__class__.__name__)
        return None
    finally:
        db.close()
