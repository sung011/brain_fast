"""
무릎 MRI "열람 자산" 등록부(여러 장을 뇌처럼 휠·슬라이더로 넘겨 보게 하는 무릎 전용 표). 설계: designs/MSN-20261015-002/KNEE_MRI_SERIES_PLAN.md 6장.

- 이 표는 "**이 문제(study)의 등록된 여러 장을 학습자에게 보여 줘도 된다**"는 자산 기록이다. 장별 영상 해시(SHA-256)와 치수를 보유한다.
  대표 1장의 기준 ROI 승인(`slide_references.py`)이나 임상 검증과 **섞지 않는다**: 열람 자산이 있다고 어느 장이든 채점되는 것이 아니다.
- 채점 기능이 꺼져 있거나 기준 ROI 가 비어 있어도, 여기에 등록된 시리즈는 탐색할 수 있다(설계 6장). 반대로 이 표가 비어 있으면(기본)
  어떤 문제도 여러 장 열람을 제공하지 않는다. **기본은 비어 있다.** 실제 등록은 영상 권리 확인과 사용자 승인 뒤에 사람이 채운다.
- 목록(manifest)과 어긋나면(경로 순서·내용 버전이 바뀜) 옛 등록을 새 목록에 쓰지 않는다(없음 처리).
- `kind` 는 실제 자료와 합성 시험을 구분한다: "real"(공개 데이터셋 영상 등) / "synthetic"(합성 시험). 합성을 실제로 속여 표시하지 않는다.
- 학습자에게 보내는 값은 불투명한 `series_id` 와 안내 문구뿐이다. 경로·해시·출처 메모는 보내지 않는다.

이 파일은 DB·NAS·모델·torch 를 쓰지 않는다.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

from .slice_manifest import SliceManifest

_SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_SIDE = 4096
KINDS = ("real", "synthetic")


@dataclass(frozen=True)
class SeriesAssets:
    study_idx: int
    manifest_version: str           # 등록할 때의 목록 버전(SliceManifest.version)
    slice_set_sha256: str           # 등록할 때의 순서 있는 경로 배열 해시
    slice_sha256: tuple             # 장별 영상 파일 전체의 SHA-256(배열 순서와 같다)
    slice_sizes: tuple              # 장별 (가로, 세로) px
    kind: str                       # real | synthetic
    notice: str                     # 학습자에게 보여 줄 출처·조건 안내(환자 식별값·비밀 금지)
    version: str                    # 등록 버전
    registered_by: str              # 프로젝트 내부 가명 코드(실명·이메일 금지). 학습자 응답에 보내지 않는다.
    registered_at: str
    status: str = "A"               # A 등록 / R 철회


# study.idx → 등록 행 목록. 비어 있다 = 어떤 문제도 여러 장 열람을 제공하지 않는다.
# 실제 등록 행은 사람이 채우는 데이터 파일(`series_registered_data.py`)에 있고, 그 파일이 비어 있으면 이 표도 비어 있다.
APPROVED_SERIES_ASSETS: dict[int, list[SeriesAssets]] = {}


@dataclass(frozen=True)
class SeriesResolution:
    status: str                     # ok | none | ambiguous
    assets: SeriesAssets | None = None
    detail: str = ""                # 내부 진단용(사용자에게 보내지 않는다)


def _stale_reason(row: SeriesAssets, study_idx: int, manifest: SliceManifest) -> str:
    if row.study_idx != study_idx:
        return "study"
    if row.manifest_version != manifest.version or row.slice_set_sha256 != manifest.list_sha256:
        return "manifest"
    if not isinstance(row.slice_sha256, tuple) or len(row.slice_sha256) != manifest.count:
        return "hash_count"
    if any(not isinstance(h, str) or not _SHA256.fullmatch(h) for h in row.slice_sha256):
        return "hash_format"
    if not isinstance(row.slice_sizes, tuple) or len(row.slice_sizes) != manifest.count:
        return "size_count"
    for size in row.slice_sizes:
        if not (isinstance(size, tuple) and len(size) == 2 and all(isinstance(v, int) and not isinstance(v, bool) and 0 < v <= MAX_SIDE for v in size)):
            return "size_value"
    if row.kind not in KINDS:
        return "kind"
    if not all(isinstance(t, str) and t.strip() for t in (row.notice, row.version, row.registered_by, row.registered_at)):
        return "record"
    return ""


def resolve(study_idx: int, manifest: SliceManifest) -> SeriesResolution:
    """현재 목록에 대해 이 문제의 등록된 열람 자산을 찾는다. 철회(R)는 없는 것으로 본다. 둘 이상이면 고르지 않고 ambiguous."""
    rows = [row for row in APPROVED_SERIES_ASSETS.get(study_idx, []) if row.status == "A"]
    if not rows:
        return SeriesResolution("none", detail="no_active_row")
    if len(rows) > 1:
        return SeriesResolution("ambiguous", detail=f"{len(rows)}_active_rows")
    reason = _stale_reason(rows[0], study_idx, manifest)
    if reason:
        return SeriesResolution("none", detail=f"stale:{reason}")
    return SeriesResolution("ok", rows[0])


def conflicts_with_reference(assets: SeriesAssets, reference) -> bool:
    """열람 시리즈의 대표 장(기준이 가리키는 장)이 채점 기준(승인 행)과 **같은 영상**이 아니면 True.

    열람 등록부는 장별 해시를, 채점 승인표는 대표 장의 해시를 따로 가진다. 둘이 서로 다른 때(영상이 새로 바뀌었는데 기준은 옛것, 또는 그 반대)
    사용자는 새 영상을 보는데 서버는 옛 영상의 기준으로 채점하게 되므로 채점을 닫는다(영상 탐색은 열어 둔다). 장 번호·해시·치수를 모두 본다.
    기준에 치수가 없으면 해시만 본다. series_id 문자열 비교로 대신하지 않는다(series_id 는 열람 등록부에만 결속된다).
    """
    index = getattr(reference, "slice_no", None)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(assets.slice_sha256):
        return True
    if assets.slice_sha256[index] != reference.image_sha256:
        return True
    width, height = getattr(reference, "image_width", None), getattr(reference, "image_height", None)
    if width is not None and height is not None and tuple(assets.slice_sizes[index]) != (width, height):
        return True
    return False


def series_id(key: bytes, *, study_idx: int, manifest: SliceManifest, assets: SeriesAssets) -> str:
    """"같은 시리즈를 보고 있는가"를 화면이 비교하는 불투명한 식별값(20자리 16진수).

    문제·순서 있는 목록 버전·장별 영상 해시에서 키 있는 해시(HMAC)로 만든다. 사용자·시각과 무관하므로 같은 시리즈는 다시 열어도 같은 값이고,
    목록이나 어느 한 장의 내용이 바뀌면 달라진다. 키 없이는 경로·해시를 거꾸로 알 수 없다.
    """
    material = "|".join(("knee-series-v1", str(study_idx), manifest.version, ",".join(assets.slice_sha256)))
    return hmac.new(key, material.encode("utf-8"), hashlib.sha256).hexdigest()[:20]


def _load_registered_rows() -> None:
    """`series_registered_data.REGISTERED_ROWS` 의 행을 등록부에 올린다(자체 점검용 `paths` 는 제외). 파일이 없거나 비어 있으면 아무것도 하지 않는다.

    데이터 묶음 선택(data_profile, FIVE-05): local 이면 위 데이터 파일, nas_presentation 이면 확정 JSON 묶음의 열람 행만,
    profile 이 잘못됐거나 묶음에 문제가 있으면 아무것도 올리지 않는다(기존 데이터로 대신하지 않음).
    """
    from . import data_profile

    chosen = data_profile.state()
    if chosen["registered"] is not None:
        rows = chosen["registered"]
    else:
        try:
            from . import series_registered_data as data
        except ImportError:
            return
        rows = data.REGISTERED_ROWS
    for row in rows:
        fields = {k: v for k, v in row.items() if k != "paths"}
        fields["slice_sha256"] = tuple(fields["slice_sha256"])
        fields["slice_sizes"] = tuple(tuple(size) for size in fields["slice_sizes"])
        APPROVED_SERIES_ASSETS.setdefault(fields["study_idx"], []).append(SeriesAssets(**fields))


_load_registered_rows()

