"""
단일 이미지 공개 게이트 표.

무릎 분류 모델은 원래 촬영면별 볼륨(슬라이스 묶음)용이라, 한 장만 넣은 결과가 믿을 만한지는 실험으로 따로 확인해야 한다.
확인된 "소견 × 촬영면 × 이미지 범위(full_slice/roi_crop)" 조합만 이 표에 승인으로 올리고, 표에 없는 조합은
API 가 점수·판정·Grad-CAM 을 만들지 않고 `not_evaluated` 로 답한다.

승인하려면 다음이 모두 있어야 한다(설계 MSN-20260930-001).
  1. 학습·선정·보정에 쓰지 않은 검사 단위 자료로 한 실험표(validation_id)와 공개 가능한 성능 수치(performance)
  2. 그 조건에서 새로 구한 단일 이미지용 보정값(calibration)과 판단 어려움 구간(band)
  3. 사람이 검수한 고정 설명(explanation_id → explanations.EXPLANATIONS)과 검수 기록(reviewed_by)

현재 실험(code/outputs/single_slice_experiment, 2026-09-30)에서는 33개 조합이 모두 통과하지 못했으므로 표는 비어 있다.
이 파일은 torch 를 불러오지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass

from .explanations import EXPLANATIONS

GATES_VERSION = "2026-09-30.0"  # 표를 바꿀 때마다 올린다.

FINDINGS = ("abnormal", "acl", "meniscus")
PLANES = ("axial", "coronal", "sagittal")
SCOPES = ("full_slice", "roi_crop")

# 소견마다 원래(볼륨) 판단에 쓰는 촬영면. combiner.json 과 같은 구성이다. 반월판은 sagittal 모델을 서비스하지 않는다.
FINDING_PLANES: dict[str, tuple[str, ...]] = {
    "abnormal": PLANES,
    "acl": PLANES,
    "meniscus": ("axial", "coronal"),
}


@dataclass(frozen=True)
class GateRecord:
    finding: str
    plane: str
    scope: str
    validation_id: str            # 승인 근거가 된 실험표 식별자
    validated_condition: str      # 어떤 조건에서 검증했는지(사람이 읽는 문장)
    model_id: str                 # 평가에 쓴 모델·전처리 식별자
    calibration: tuple[float, float]  # (platt_a, platt_b): 단일 이미지용으로 새로 구한 값
    band: tuple[float, float]     # 판단 어려움 구간(낮음, 높음)
    performance: dict             # 공개할 검증 수치(표기 전 검수)
    explanation_id: str           # explanations.EXPLANATIONS 의 키
    reviewed_by: str              # 검수 기록


@dataclass(frozen=True)
class LocationGate:
    plane: str
    scope: str
    validation_id: str
    validated_condition: str
    model_id: str
    performance: dict
    reviewed_by: str


# 승인된 조합. 비어 있으면 검수된 보정·성능 근거는 없다. 화면 분석은 그때도 모델이 있는 면의 원래 점수를 학습용으로 낸다.
APPROVED: dict[tuple[str, str, str], GateRecord] = {}
APPROVED_LOCATION: dict[tuple[str, str], LocationGate] = {}


def model_plane_available(finding: str, plane: str) -> bool:
    """이 소견을 그 촬영면에서 판단하는 모델이 서비스에 있는가."""
    return plane in FINDING_PLANES.get(finding, ())


def find_gate(finding: str, plane: str, scope: str) -> GateRecord | None:
    """승인되어 있고, 공개에 필요한 것(성능 수치·검수된 설명·검수 기록)이 모두 있는 조합만 돌려준다."""
    record = APPROVED.get((finding, plane, scope))
    if record is None:
        return None
    if not record.performance or not record.reviewed_by or record.explanation_id not in EXPLANATIONS:
        return None
    return record


def find_location_gate(plane: str, scope: str) -> LocationGate | None:
    record = APPROVED_LOCATION.get((plane, scope))
    if record is None or not record.performance or not record.reviewed_by:
        return None
    return record
