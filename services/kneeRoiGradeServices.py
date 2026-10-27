"""
무릎 학습 ROI 채점 (좌표만 비교한다. 이미지도 AI 모델도 쓰지 않는다).

- 기준 ROI 는 class/knee/reference_rois.py 에 있는 "사람이 검수·승인한 케이스별 기준"뿐이다(D-069, D-071).
  표에 없으면 채점하지 않는다(graded=False). AI 가 찾은 박스를 기준으로 쓰지 않는다.
- 도형은 박스만 받는다(사용자 결정 2026-10-06). 박스는 이미지 안(0~1)에 있어야 한다.
- 문제 이미지가 한 장일 때만 채점한다. 여러 장(슬라이드, st_image 가 JSON 배열)은 채점하지 않는다(A안, slide_not_supported).
- 판정의 원본은 **정수 픽셀 수**다(GRID×GRID 격자에서 겹친 칸 수 inter 와 합친 칸 수 union).
  IoU≥0.5 정답, 0.2≤IoU<0.5 부분정답, 그 미만 오답(D-069)을 inter·union 정수 비교로만 판정하고,
  응답의 iou(실수)는 표시용이다. 규칙 버전을 응답에 기록한다.
  (뇌 채점 services/roiGradeServices.py 의 재현율 조건은 쓰지 않는다. 큰 박스가 재현율만으로 정답이 되는 것을 막는다.)
- 사용자 ROI 좌표는 이미지 전체를 0~1 로 본 값(normalized)만 받는다.
이 파일은 torch 를 불러오지 않는다.
"""
from __future__ import annotations

import math

import numpy as np

from services import roiGradeServices as roi_grade

RULE_VERSION = "D-069/2026-09-16"
CORRECT_IOU = 0.5     # 표시·응답용. 판정은 아래 정수 분수로 한다.
PARTIAL_IOU = 0.2
CORRECT_FRACTION = (1, 2)   # IoU ≥ 1/2 ⇔ inter·2 ≥ union
PARTIAL_FRACTION = (1, 5)   # IoU ≥ 1/5 ⇔ inter·5 ≥ union
GRID = 512  # 좌표를 이 크기의 격자에 옮겨 겹침을 센다(0~1 좌표라 원본 이미지 크기와 무관)
EDGE_TOLERANCE = 1e-6  # 이미지 가장자리 판정에서 소수 표기 오차를 흡수하는 크기

NOTICES = [
    "이 채점은 학습용입니다. 기준 영역은 사람이 검수한 케이스 기준이며 진단이 아닙니다.",
    "겹침(IoU)만으로 점수를 정합니다. 기준 영역과 얼마나 겹치는지 확인하는 연습입니다.",
]
NOT_GRADED_MESSAGES = {
    "reference_not_approved": "이 문제는 승인된 기준 영역이 아직 준비되지 않아 채점하지 않습니다.",
    "slide_not_supported": "이 문제는 여러 장(슬라이드)으로 되어 있어 아직 채점하지 않습니다.",
    "study_image_unavailable": "이 문제의 이미지를 확인할 수 없어 채점하지 않습니다.",
    # 대표 슬라이스 방식(슬라이드 문제). 사용자에게는 내부 원인을 알리지 않는 고정 문구만 보낸다.
    "no_reference_for_slice": "이 슬라이스에는 승인된 기준 영역이 없어 채점하지 않습니다. 대표 영상을 확인해 주세요.",
    "reference_ambiguous": "이 문제의 기준 영역 정보를 확인할 수 없어 채점하지 않습니다.",
    "token_invalid": "문제 화면 정보를 확인할 수 없어 채점하지 않습니다. 문제를 다시 열어 주세요.",
    "token_expired": "문제 화면의 유효 시간이 지났습니다. 문제를 다시 열어 주세요.",
    "presentation_changed": "문제 내용이 바뀌어 채점하지 않습니다. 문제를 다시 열어 주세요.",
    "asset_changed": "이 문제의 영상을 확인할 수 없어 채점하지 않습니다.",
    # 여러 장 열람 영상과 채점 기준이 같은 영상이 아닐 때(MSN-20261015-002 FINDING-SERIES-002-01). 영상 탐색은 열려 있다.
    "series_reference_mismatch": "보이는 영상과 채점 기준이 서로 맞지 않아 채점하지 않습니다. 영상은 계속 넘겨 볼 수 있어요.",
}
NOT_GRADED_MESSAGE = NOT_GRADED_MESSAGES["reference_not_approved"]
BOX_ONLY_MESSAGE = "박스만 사용할 수 있습니다. roi_type 은 box 여야 합니다."


def _finite(*values) -> None:
    for value in values:
        if value is not None and not math.isfinite(float(value)):
            raise ValueError("좌표는 유한한 숫자여야 합니다.")


def user_mask(
    *,
    roi_type: str,
    x: float | None = None,
    y: float | None = None,
    width: float | None = None,
    height: float | None = None,
) -> np.ndarray:
    """사용자 박스(0~1 좌표) → GRID×GRID 마스크. 박스가 아니거나 올바르지 않으면 ValueError."""
    if (roi_type or "").strip().lower() != "box":
        raise ValueError(BOX_ONLY_MESSAGE)
    if x is None or y is None or width is None or height is None:
        raise ValueError("박스는 x, y, width, height 가 필요합니다.")
    _finite(x, y, width, height)
    if width <= 0 or height <= 0:
        raise ValueError("박스 크기가 올바르지 않습니다.")
    if x < -EDGE_TOLERANCE or y < -EDGE_TOLERANCE or x + width > 1 + EDGE_TOLERANCE or y + height > 1 + EDGE_TOLERANCE:
        raise ValueError("박스는 이미지 안에 있어야 합니다.")
    return roi_grade.roi_mask(
        GRID, GRID, roi_type="box", normalized=True, x=x, y=y, box_w=width, box_h=height,
    )


def single_image_path(st_image) -> tuple[str | None, str | None]:
    """문제 이미지 값 → (단일 이미지 경로, 채점 불가 사유). 둘 중 하나만 값이 있다.

    - 경로 문자열 하나: 앞뒤 공백을 뺀 경로를 돌려준다.
    - JSON 배열처럼 보이는 값('[' 로 시작): 슬라이드 문제라 slide_not_supported.
    - 비었거나 문자열이 아니거나 JSON 객체·따옴표 문자열처럼 보이는 값: study_image_unavailable.
    """
    text = st_image.strip() if isinstance(st_image, str) else ""
    if not text:
        return None, "study_image_unavailable"
    if text.startswith("["):
        return None, "slide_not_supported"
    if text[0] in '{"':
        return None, "study_image_unavailable"
    return text, None


def reference_mask(ref) -> np.ndarray:
    x1 = max(0, min(GRID, int(round(ref.x * GRID))))
    x2 = max(0, min(GRID, int(round((ref.x + ref.width) * GRID))))
    y1 = max(0, min(GRID, int(round(ref.y * GRID))))
    y2 = max(0, min(GRID, int(round((ref.y + ref.height) * GRID))))
    mask = np.zeros((GRID, GRID), dtype=bool)
    if x2 > x1 and y2 > y1:
        mask[y1:y2, x1:x2] = True
    return mask


def verdict_from_px(inter: int, union: int) -> str:
    """겹친 칸 수(inter)와 합친 칸 수(union)로만 판정한다. 실수 IoU·반올림값은 쓰지 않는다."""
    for value in (inter, union):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("겹침 픽셀 수는 정수여야 합니다.")
    if union <= 0 or inter < 0 or inter > union:
        raise ValueError("겹침 픽셀 수가 올바르지 않습니다.")
    if inter * CORRECT_FRACTION[1] >= union * CORRECT_FRACTION[0]:
        return "정답"
    if inter * PARTIAL_FRACTION[1] >= union * PARTIAL_FRACTION[0]:
        return "부분정답"
    return "오답"


def stored_box_from_mask(mask: np.ndarray) -> dict:
    """채점에 쓴 마스크의 정수 경계에서 DB 에 저장할 정규화 박스(6자리)를 만든다.

    요청 원좌표를 그대로 저장하거나 잘라서(clip) 반올림하지 않는다. 마스크가 기준이라 저장값을 다시 래스터라이즈하면
    같은 마스크·같은 겹침 칸 수·같은 판정이 재현된다(설계 7장 좌표 저장 계약). 지금은 저장하지 않고 변환만 제공한다.
    """
    ys, xs = np.where(mask)
    if xs.size == 0:
        raise ValueError("빈 마스크는 저장할 수 없습니다.")
    x1, x2, y1, y2 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1   # 끝은 포함하지 않는 칸 번호
    return {
        "x": round(x1 / GRID, 6), "y": round(y1 / GRID, 6),
        "width": round((x2 - x1) / GRID, 6), "height": round((y2 - y1) / GRID, 6),
    }


def _norm_bbox(mask: np.ndarray) -> dict | None:
    box = roi_grade._bbox_from_mask(mask)
    if box is None:
        return None
    return {"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"]}


def _base(user: np.ndarray, plane: str | None) -> dict:
    return {
        "ok": True,
        "graded": False,
        "not_graded_reason": None,
        "not_graded_message": None,
        "result": None,
        "iou": None,
        "overlap_px": None,
        "review_idx": None,   # 미채점이면 null. 채점되어 저장되면 라우터가 실제 번호로 바꾼다(설계 4-3 미채점 응답 계약)
        "rule": {"metric": "iou", "version": RULE_VERSION, "correct_iou": CORRECT_IOU, "partial_iou": PARTIAL_IOU},
        "user_roi_bbox": _norm_bbox(user),
        "reference_bbox": None,
        "reference": None,
        "body_part": "knee",
        "plane": plane,
        "notices": list(NOTICES),
    }


def not_graded(user: np.ndarray, reason: str) -> dict:
    """채점하지 않는 응답. 정답·점수·기준 정보를 담지 않는다."""
    out = _base(user, None)
    out.update(not_graded_reason=reason, not_graded_message=NOT_GRADED_MESSAGES[reason])
    return out


def grade(user: np.ndarray, ref) -> dict:
    """승인된 기준 ROI(ref) 가 있으면 채점하고, 없으면(None) graded=False 로 답한다."""
    if ref is None:
        return not_graded(user, "reference_not_approved")
    ref_mask = reference_mask(ref)
    if not ref_mask.any():  # 격자에서 0칸이 되는 기준은 승인으로 보지 않는다(모든 답이 오답이 되는 것을 막는다)
        return not_graded(user, "reference_not_approved")

    inter = int(np.logical_and(user, ref_mask).sum())
    union = int(np.logical_or(user, ref_mask).sum())
    out = _base(user, ref.plane)
    out.update(
        graded=True,
        result=verdict_from_px(inter, union),
        iou=round(inter / union, 5),  # 표시용. 판정에는 쓰지 않는다.
        overlap_px={"grid_size": GRID, "intersection": inter, "union": union},
        reference_bbox={"x": ref.x, "y": ref.y, "width": ref.width, "height": ref.height},
        reference={"kind": "case_reference_roi", "source": ref.source, "version": ref.version,
                   "coord_system": ref.coord_system},
    )
    return out
