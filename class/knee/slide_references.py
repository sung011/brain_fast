"""
무릎 슬라이드 문제의 기준 ROI 표(대표 슬라이스 1장 방식)와 해석 규칙. 비활성 골격이다.

설계: designs/MSN-20260930-001/REPRESENTATIVE_SLICE_CONTRACT.md 3-2.

- 문제(study)마다 **승인된 기준 1개 = 대표 슬라이스 1장**만 허용한다. 승인이 둘 이상이면 어느 쪽도 고르지 않고
  `ambiguous` 로 답한다(첫 행을 임의로 쓰지 않는다).
- 기준이 현재 목록(manifest)과 어긋나면(경로·장 번호·목록 해시·버전) 대체 기준 없이 "없음"으로 본다.
- 이 표는 **비어 있다**. 실제 승인 등록은 KneeMRI 제공 권리 확인(HIGH)과 팀원 라벨 확인 절차가 끝난 뒤 별도 승인으로 한다.
- AI 가 낸 박스를 검수 없이 기준으로 올리지 않는다(D-071). 이 표의 박스는 데이터셋 라벨을 팀원이 확인한 값이다.

이 파일은 DB·NAS·모델·torch 를 쓰지 않는다.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .reference_rois import COORD_SYSTEM, PLANES
from .slice_manifest import SliceManifest, SliceManifestError, candidate_source_z, source_z_to_index, validate_source_numbers

_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class SlideReference:
    reference_id: int
    study_idx: int
    manifest_version: str      # 승인할 때의 목록 버전(SliceManifest.version)
    slice_set_sha256: str      # 승인할 때의 순서 있는 경로 배열 해시
    slice_no: int              # 대표 슬라이스의 등록 배열 위치(0부터)
    image_path: str            # 대표 장의 서버 경로(배열의 slice_no 번째와 같아야 함)
    image_sha256: str          # 최종 제공 파일 전체의 SHA-256(소문자 16진수 64자)
    plane: str
    x: float                   # 왼쪽 위 기준 정규화 좌표(0~1)
    y: float
    width: float
    height: float
    source: str                # 공개 데이터셋 레코드·선정/변환 규칙(환자 식별값 금지)
    version: str               # 기준 버전
    reviewed_by: str           # 프로젝트 내부 가명 코드(실명·이메일 금지). 학습자 응답에 보내지 않는다.
    reviewed_at: str           # UTC 기준 시각
    coord_system: str = COORD_SYSTEM
    status: str = "A"          # A 승인 / R 철회
    source_slice_no: int | None = None   # 원본 z(기록용)
    selection_reason: str = ""           # 가운데 후보를 바꿨다면 사유
    image_width: int | None = None       # 승인할 때의 영상 가로(px). 영상 공급원(NAS/로컬)을 쓸 때는 필수이며 읽은 PNG 치수와 같아야 한다
    image_height: int | None = None      # 승인할 때의 영상 세로(px)


# study.idx → 승인 행 목록. 비어 있다 = 어떤 슬라이드 문제도 채점하지 않는다.
APPROVED_SLIDE_REFERENCES: dict[int, list[SlideReference]] = {}


@dataclass(frozen=True)
class Resolution:
    status: str                         # ok | none | ambiguous
    reference: SlideReference | None = None
    detail: str = ""                    # 내부 진단용(사용자에게 보내지 않는다)


def _box_ok(ref: SlideReference) -> bool:
    values = (ref.x, ref.y, ref.width, ref.height)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
        return False
    if ref.width <= 0 or ref.height <= 0 or ref.x < 0 or ref.y < 0:
        return False
    return ref.x + ref.width <= 1 + 1e-9 and ref.y + ref.height <= 1 + 1e-9


def _stale_reason(ref: SlideReference, study_idx: int, manifest: SliceManifest) -> str:
    """승인 행이 현재 목록에 적용되지 않는 이유(없으면 빈 문자열)."""
    if ref.study_idx != study_idx:
        return "study"
    if not isinstance(ref.slice_no, int) or isinstance(ref.slice_no, bool) or not 0 <= ref.slice_no < manifest.count:
        return "slice_no"
    if ref.manifest_version != manifest.version or ref.slice_set_sha256 != manifest.list_sha256:
        return "manifest"
    if manifest.paths[ref.slice_no] != ref.image_path:
        return "path"
    if not isinstance(ref.image_sha256, str) or not _SHA256.fullmatch(ref.image_sha256):
        return "image_sha256"
    if ref.plane not in PLANES or ref.coord_system != COORD_SYSTEM:
        return "plane_or_coord"
    if not all(isinstance(t, str) and t.strip() for t in (ref.source, ref.version, ref.reviewed_by, ref.reviewed_at)):
        return "review_record"
    if not _box_ok(ref):
        return "box"
    sizes = (ref.image_width, ref.image_height)
    if (sizes[0] is None) != (sizes[1] is None) or any(v is not None and (not isinstance(v, int) or isinstance(v, bool) or v <= 0) for v in sizes):
        return "image_size"
    return ""


def resolve(study_idx: int, manifest: SliceManifest) -> Resolution:
    """현재 목록에 대해 이 문제의 승인된 대표 기준을 찾는다.

    - 철회(R)된 행은 없는 것으로 본다.
    - 승인(A)이 둘 이상이면 ambiguous. 하나인데 현재 목록과 어긋나면 none(옛 승인을 새 목록에 억지로 쓰지 않는다).
    """
    rows = [row for row in APPROVED_SLIDE_REFERENCES.get(study_idx, []) if row.status == "A"]
    if not rows:
        return Resolution("none", detail="no_active_row")
    if len(rows) > 1:
        return Resolution("ambiguous", detail=f"{len(rows)}_active_rows")
    reason = _stale_reason(rows[0], study_idx, manifest)
    if reason:
        return Resolution("none", detail=f"stale:{reason}")
    return Resolution("ok", rows[0])


# ── 등록 후보 검증(실제 등록은 하지 않는다) — 설계 2장 ─────────────────────────────
class ApprovalCandidateError(SliceManifestError):
    """등록 후보가 규칙에 맞지 않아 승인 대상으로 쓸 수 없을 때."""


def candidate_from_dataset_roi(
    *,
    manifest: SliceManifest,
    source_numbers,
    roi_x: int,
    roi_y: int,
    roi_z: int,
    roi_width: int,
    roi_height: int,
    roi_depth: int,
    volume_slices: int,
    image_width: int,
    image_height: int,
) -> dict:
    """데이터셋 ROI(원본 픽셀 박스 + z 범위)에서 승인 후보 값을 계산한다. 잘못된 값은 거절하고 고쳐 쓰지 않는다.

    - 대표 후보 z = roiZ + roiDepth // 2 → 등록 배열 위치로 매핑한 값이 slice_no.
    - 박스는 전체 영상 폭 W·높이 H 로 나눈 왼쪽 위 기준 정규화 좌표다(YOLO 중심 좌표를 그대로 쓰지 않는다).
    - 영상 밖으로 나간 박스는 자르지 않고 거절한다(잘못된 메타데이터를 숨기지 않는다).
    돌려주는 dict 는 후보일 뿐 승인이 아니다(검수 기록·이미지 해시·버전은 별도로 채운다).
    """
    ints = (roi_x, roi_y, roi_z, roi_width, roi_height, roi_depth, volume_slices, image_width, image_height)
    if any(not isinstance(v, int) or isinstance(v, bool) for v in ints):
        raise ApprovalCandidateError("ROI 값은 정수여야 합니다.")
    if image_width <= 0 or image_height <= 0 or roi_width <= 0 or roi_height <= 0 or roi_x < 0 or roi_y < 0:
        raise ApprovalCandidateError("박스 또는 영상 크기가 올바르지 않습니다.")
    if roi_x + roi_width > image_width or roi_y + roi_height > image_height:
        raise ApprovalCandidateError("박스가 영상 밖으로 나갑니다.")
    numbers = validate_source_numbers(source_numbers, manifest.count, volume_slices)   # 후보를 만들기 전에 표 전체를 검사한다
    source_z = candidate_source_z(roi_z, roi_depth, volume_slices)
    slice_no = source_z_to_index(source_z, numbers)
    if slice_no >= manifest.count:
        raise ApprovalCandidateError("대표 후보가 목록 범위 밖입니다.")
    return {
        "slice_no": slice_no,
        "source_slice_no": source_z,
        "image_path": manifest.paths[slice_no],
        "slice_set_sha256": manifest.list_sha256,
        "manifest_version": manifest.version,
        "x": roi_x / image_width,
        "y": roi_y / image_height,
        "width": roi_width / image_width,
        "height": roi_height / image_height,
        "image_width": image_width,
        "image_height": image_height,
    }


# ── 실제 승인 행 불러오기 ─────────────────────────────────────────────────────────────
def _load_reference_rows() -> None:
    """`slide_references_data.REFERENCE_ROWS`(사람이 승인해 채우는 데이터 파일)의 행을 승인표에 올린다.

    파일이 없거나 비어 있으면 아무것도 하지 않는다(기본: 빈 표 = 어떤 문제도 채점하지 않는다). 행 형식이 틀리면 조용히 넘기지 않고
    불러오기에서 실패한다(잘못된 승인이 섞인 채 서버가 뜨지 않게). 행이 승인 규칙(_stale_reason)에 맞는지는 채점 때마다 다시 확인한다.
    """
    import importlib

    from . import data_profile

    chosen = data_profile.state()   # FIVE-05: nas_presentation 이면 확정 묶음의 승인 행만, 닫힘이면 빈 표(local 데이터로 대신하지 않음)
    if chosen["reference"] is not None:
        for row in chosen["reference"]:
            APPROVED_SLIDE_REFERENCES.setdefault(row["study_idx"], []).append(SlideReference(**row))
        return
    name = f"{__package__}.slide_references_data"
    try:
        data = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:   # 데이터 파일 자체가 없을 때만 빈 표로 둔다(FINDING-REFERENCE-004)
            return
        raise                  # 데이터 파일 안에서 다른 모듈을 못 찾는 등 불러오기 오류는 숨기지 않는다
    for row in data.REFERENCE_ROWS:
        APPROVED_SLIDE_REFERENCES.setdefault(row["study_idx"], []).append(SlideReference(**row))


_load_reference_rows()
