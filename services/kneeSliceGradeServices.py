"""
무릎 슬라이드 문제의 대표 슬라이스 채점 판단과 제시 응답 빌더 (비활성 골격).

설계: designs/MSN-20260930-001/REPRESENTATIVE_SLICE_CONTRACT.md 4장.
라우터(routers/kneeLearning.py)는 HTTP 처리만 하고, "이 요청을 채점해도 되는가"의 판단은 여기서 한다.
DB·NAS·모델·torch 를 쓰지 않는다. 승인 표(class/knee/slide_references.py)는 비어 있어서 지금은 어떤 슬라이드 문제도
채점되지 않는다. 시험은 합성 데이터를 임시로 끼워 넣어 확인한다.

판단 순서(설계 4-3 의 3~5):
  1) slice_index 형식(400) → 2) 목록 검사(쓸 수 없으면 미채점) → 3) 범위(400)
  4) 승인 기준 조회: 없음/모호 → 미채점(토큰 요구 없음)
  5) 토큰 서명·만료·사용자·문제, 목록 버전·목록 해시 대조 → 미채점 사유
  6) 대표가 아닌 장 → 미채점(no_reference_for_slice, 저장 없음)
  7) 대표 장이면 토큰의 대표 번호·내용 해시·기준 ID/버전 대조 → 자산 확인 훅 → 채점
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from importlib import import_module

from services import kneeRoiGradeServices as knee_grade

SCHEMA_VERSION = "knee-representative-v1"
TASK_LABEL = "ACL이 포함된 참고 영역 표시 연습"
REFERENCE_BASIS = "dataset_acl_region"
# 자산 확인 훅이 돌려줄 수 있는 사유(그 밖의 값은 무시하지 않고 안전하게 asset_changed 로 본다)
_ASSET_REASONS = {"study_image_unavailable", "asset_changed", "series_reference_mismatch"}


def _knee(name: str):
    return import_module(f"class.knee.{name}")   # 패키지 이름이 class 라서 import 문을 쓸 수 없다


class SlideRequestError(Exception):
    """HTTP 오류로 바꿔야 하는 경우(400 입력 오류, 503 서버 구성). 문구에 내부 원인·토큰을 담지 않는다."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class SlideOutcome:
    result: dict
    image_path: str | None   # 채점되어 저장할 때만 값이 있다: 대표 장의 서버 단일 경로
    snapshot: tuple | None = None   # 채점에 실제로 쓴 (manifest, 승인 기준). 호출하는 쪽이 저장 전에 현재 DB 와 다시 대조한다


def _not_graded(user_mask, reason: str) -> SlideOutcome:
    return SlideOutcome(knee_grade.not_graded(user_mask, reason), None)


def grade_slide(
    *,
    user_mask,
    user_idx: int,
    study_idx: int,
    st_image,
    slice_index_raw,
    problem_token,
    now: int,
    asset_verifier=None,
) -> SlideOutcome:
    """슬라이드 문제 한 건의 제출을 판단한다. 채점하지 않는 경우에는 저장할 경로가 없다(image_path=None).

    asset_verifier(reference, manifest) -> None | 사유: NAS 접근은 팀장·NAS 시험 뒤에 연결한다.
    기본값 None 은 "자산 확인을 하지 않음"이며 확인했다는 뜻이 아니다.
    """
    manifest_mod, refs, tokens, config = _knee("slice_manifest"), _knee("slide_references"), _knee("problem_token"), _knee("config")

    try:
        index = manifest_mod.parse_slice_index(slice_index_raw)
    except manifest_mod.SliceIndexError as exc:
        raise SlideRequestError(400, str(exc)) from None

    try:
        manifest = manifest_mod.build_manifest(st_image, config.asset_roots())
    except manifest_mod.SliceManifestError:
        return _not_graded(user_mask, "study_image_unavailable")
    if index >= manifest.count:
        raise SlideRequestError(400, "slice_index 가 슬라이드 범위를 벗어났습니다.")

    resolution = refs.resolve(study_idx, manifest)
    if resolution.status == "none":
        return _not_graded(user_mask, "no_reference_for_slice")
    if resolution.status == "ambiguous":
        return _not_graded(user_mask, "reference_ambiguous")
    ref = resolution.reference

    key = config.problem_token_key()
    if key is None:
        raise SlideRequestError(503, "이 기능은 지금 사용할 수 없습니다.")
    try:
        claims = tokens.verify_token(problem_token, key=key, now=now, user_idx=user_idx, study_idx=study_idx)
    except tokens.TokenExpired:
        return _not_graded(user_mask, "token_expired")
    except tokens.TokenInvalid:
        return _not_graded(user_mask, "token_invalid")
    if claims["mv"] != manifest.version or claims["ls"] != manifest.list_sha256:
        return _not_graded(user_mask, "presentation_changed")

    if index != ref.slice_no:   # 범위 안의 유효한 장이지만 대표가 아니다. 대표로 바꿔 계산하지 않는다.
        return _not_graded(user_mask, "no_reference_for_slice")
    if (claims["sn"], claims["is"], claims["ri"], claims["rv"]) != (ref.slice_no, ref.image_sha256, ref.reference_id, ref.version):
        return _not_graded(user_mask, "presentation_changed")

    if asset_verifier is not None:
        reason = asset_verifier(ref, manifest)
        if reason:
            return _not_graded(user_mask, reason if reason in _ASSET_REASONS else "asset_changed")

    result = knee_grade.grade(user_mask, ref)
    if not result["graded"]:
        return SlideOutcome(result, None)
    result.update(
        slice_index=index,
        rule_version=knee_grade.RULE_VERSION,
        reference_basis=REFERENCE_BASIS,
        clinical_validated=False,
    )
    return SlideOutcome(result, ref.image_path, (manifest, ref))


def _task_base() -> dict:
    return {
        "schema_version": SCHEMA_VERSION, "mode": "representative_slice",
        "representative_slice_index": None, "display_slice_number": None, "slice_count": None,
        "image_url": None, "image_width": None, "image_height": None,
        "coord_system": _knee("reference_rois").COORD_SYSTEM,
        "grading_available": False, "unavailable_reason": None, "problem_token": None,
        "presentation_id": None, "task_label": TASK_LABEL, "series_id": None,
    }


def build_unavailable_task(*, study_idx: int, reason: str, slice_count: int | None = None) -> dict:
    """채점할 수 없는 제시(토큰·영상 주소 없음). 영상 탐색(여러 장 열람)은 별도 주소로 계속 가능하다."""
    return {"study_idx": study_idx, "knee_task": dict(_task_base(), slice_count=slice_count, unavailable_reason=reason)}


def presentation_id(key: bytes, *, study_idx: int, manifest, reference) -> str:
    """"같은 영상을 보고 있는가"를 화면이 비교할 수 있는 불투명한 식별값(16자리 16진수).

    문제·목록 버전·대표 번호·영상 내용 해시에서 키 있는 해시(HMAC)로 만든다. 사용자·시각·토큰과 무관해서 같은 제시는 다시
    열어도 같은 값이고, 대표 장이나 영상 내용이 바뀌면 달라진다. 키 없이는 경로·해시·번호를 거꾸로 알 수 없다.
    기준 번호·버전은 넣지 않는다(같은 영상에서 기준 박스 버전만 바뀐 경우는 화면 박스를 그대로 써도 되기 때문이다).
    """
    material = "|".join(("knee-presentation-v1", str(study_idx), manifest.version, str(reference.slice_no), reference.image_sha256))
    return hmac.new(key, material.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def build_knee_task(
    *,
    user_idx: int,
    study_idx: int,
    st_image,
    image_url,
    image_width,
    image_height,
    now: int,
) -> dict:
    """제시 응답의 `knee_task` 를 만든다(팀장의 제시 API 가 호출한다). 라우트는 만들지 않는다.

    - 승인된 대표가 없거나 모호하면 학습 불가 사유만 알리고 토큰·영상 주소를 내리지 않는다(임의 장을 승인된 것처럼 보이지 않게).
    - 정답 박스·진단·출처·검수자·내부 경로는 어떤 경우에도 넣지 않는다. 경로를 바로 쓰지 않도록 image_url 은 호출하는 쪽이
      서버가 인증·대상을 검증하는 제공 주소로 바꿔 넘겨야 한다.
    - 승인된 대표가 있을 때의 발급은 `build_knee_task_for_snapshot` 이 한다. 영상 주소를 공급받은 기준과 토큰을 만드는
      기준이 같도록 공급하는 쪽(라우터)은 그 함수에 같은 snapshot 을 넘긴다.
    """
    manifest_mod, refs, config = _knee("slice_manifest"), _knee("slide_references"), _knee("config")

    def unavailable(reason: str, count: int | None = None) -> dict:
        task = dict(_task_base(), slice_count=count, unavailable_reason=reason)
        return {"study_idx": study_idx, "knee_task": task}

    try:
        manifest = manifest_mod.build_manifest(st_image, config.asset_roots())
    except manifest_mod.SliceManifestError:
        return unavailable("study_image_unavailable")
    resolution = refs.resolve(study_idx, manifest)
    if resolution.status == "none":
        return unavailable("no_reference_for_slice", manifest.count)
    if resolution.status == "ambiguous":
        return unavailable("reference_ambiguous", manifest.count)
    return build_knee_task_for_snapshot(
        manifest=manifest, reference=resolution.reference, user_idx=user_idx, study_idx=study_idx,
        image_url=image_url, image_width=image_width, image_height=image_height, now=now,
    )


def build_knee_task_for_snapshot(
    *,
    manifest,
    reference,
    user_idx: int,
    study_idx: int,
    image_url,
    image_width,
    image_height,
    now: int,
    series_id: str | None = None,
) -> dict:
    """이미 확인된 목록(manifest)과 승인 기준(reference) **그 한 벌**로 제시를 만든다(승인표를 다시 조회하지 않는다).

    series_id: 같은 목록·같은 장별 영상 등록(열람 자산)을 가리키는 불투명 식별값(없으면 None). 화면이 `GET /learning/knee/series` 의
    series_id 와 같은지 비교해 "탐색 중인 시리즈"와 "이 제시(토큰)"가 같은 목록인지 확인하는 데 쓴다. 채점 판단에는 쓰이지 않는다(토큰이 이미
    목록 버전·해시에 결속돼 있다).

    영상 주소·크기를 공급받은 기준과 토큰·대표 번호를 만든 기준이 달라지는 일을 막으려고(FINDING-SCREEN-017-03) 호출하는
    쪽이 공급과 발급에 같은 snapshot 을 넘긴다.
    """
    manifest_mod, tokens, config = _knee("slice_manifest"), _knee("problem_token"), _knee("config")
    ref = reference
    key = config.problem_token_key()
    if key is None:
        raise SlideRequestError(503, "이 기능은 지금 사용할 수 없습니다.")
    if not (isinstance(image_url, str) and image_url and image_url != ref.image_path):
        raise ValueError("image_url 은 서버가 검증하는 제공 주소여야 하며 내부 경로를 그대로 쓸 수 없습니다.")
    for dim in (image_width, image_height):
        if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
            raise ValueError("영상 크기는 양의 정수여야 합니다.")

    token = tokens.issue_token(
        key=key, now=now, ttl_s=config.problem_token_ttl_s(), user_idx=user_idx, study_idx=study_idx,
        manifest_version=manifest.version, slice_no=ref.slice_no, slice_set_sha256=manifest.list_sha256,
        image_sha256=ref.image_sha256, reference_id=ref.reference_id, reference_version=ref.version,
    )
    task = dict(
        _task_base(), representative_slice_index=ref.slice_no,
        display_slice_number=manifest_mod.display_slice_number(ref.slice_no), slice_count=manifest.count,
        image_url=image_url, image_width=image_width, image_height=image_height,
        grading_available=True, problem_token=token,
        presentation_id=presentation_id(key, study_idx=study_idx, manifest=manifest, reference=ref),
        series_id=series_id,
    )
    return {"study_idx": study_idx, "knee_task": task}
