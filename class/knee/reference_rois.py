"""
무릎 학습 채점의 기준 ROI 표(케이스 뱅크).

학습 문제(study)마다 "사람이 검수·승인한 기준 ROI" 1개가 여기에 있어야 채점한다(D-069, D-071).
AI(YOLO 등)가 낸 박스를 검수 없이 기준으로 쓰지 않는다. 표에 없거나 승인 정보가 빠졌으면 채점하지 않는다.

승인하려면 다음이 모두 있어야 한다.
  1. 출처(source): 어떤 자료·라벨에서 온 박스인지(데이터셋 이름·버전·라벨 파일 등)
  2. 버전(version): 이 기준 ROI 를 바꿀 때마다 올리는 식별자
  3. 검수 기록(reviewed_by, reviewed_at): 누가 언제 이 문제의 박스를 확인했는지
  4. 좌표: 이미지 전체를 0~1 로 본 왼쪽 위 기준 박스(coord_system), 촬영면(plane)

지금은 승인된 케이스가 없어 표가 비어 있다. 이 파일은 torch 를 불러오지 않는다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

PLANES = ("axial", "coronal", "sagittal")
COORD_SYSTEM = "normalized_image_top_left"


@dataclass(frozen=True)
class ReferenceRoi:
    study_idx: int
    plane: str
    x: float       # 왼쪽 (0~1)
    y: float       # 위쪽 (0~1)
    width: float   # 너비 (0~1)
    height: float  # 높이 (0~1)
    source: str
    version: str
    reviewed_by: str
    reviewed_at: str
    coord_system: str = COORD_SYSTEM


# study.idx → 승인된 기준 ROI. 비어 있다 = 어떤 문제도 채점하지 않는다.
APPROVED_REFERENCES: dict[int, ReferenceRoi] = {}


def _valid(ref: ReferenceRoi) -> bool:
    values = (ref.x, ref.y, ref.width, ref.height)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        return False
    if ref.width <= 0 or ref.height <= 0 or ref.x < 0 or ref.y < 0:
        return False
    if ref.x + ref.width > 1 + 1e-9 or ref.y + ref.height > 1 + 1e-9:
        return False
    return True


def find_reference(study_idx: int) -> ReferenceRoi | None:
    """승인 정보가 모두 갖춰지고 좌표가 올바른 기준 ROI 만 돌려준다."""
    ref = APPROVED_REFERENCES.get(study_idx)
    if ref is None:
        return None
    if ref.study_idx != study_idx or ref.plane not in PLANES or ref.coord_system != COORD_SYSTEM:
        return None
    if not all((text or "").strip() for text in (ref.source, ref.version, ref.reviewed_by, ref.reviewed_at)):
        return None
    return ref if _valid(ref) else None
