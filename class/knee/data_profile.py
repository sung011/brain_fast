"""
무릎 열람 등록부·채점 승인표의 **데이터 묶음 선택**(MSN-20261016-003 FIVE-05).

- `KNEE_DATA_PROFILE=local`(기본): 기존 데이터 파일(`series_registered_data.py`·`slide_references_data.py`)을 그대로 쓴다.
- `KNEE_DATA_PROFILE=nas_presentation`: `KNEE_PRESENTATION_BUNDLE` 이 가리키는 **확정된 JSON 묶음 하나**만 쓴다(임의 Python 모듈 실행 없음).
  묶음은 열람 등록 행과 승인 행을 함께 담고, 둘을 함께 검사해 함께 올린다. 하나라도 문제가 있으면 **둘 다 비운다**(닫힘).
- 그 밖의 profile 값, 파일 없음·형식 오류·후보(status≠confirmed)·내용 해시 불일치·대상 집합 불일치·중복은 모두 닫힘이며
  **기존 local 데이터로 대신하지 않는다.** 결과는 프로세스가 처음 읽을 때 한 번 정해 고정한다(실행 중 환경변수를 바꿔도 재시작 전에는 그대로).
DB·NAS·모델을 쓰지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from . import config

SCHEMA_VERSION = "knee-presentation-bundle-v1"
# 행 필드(데이터클래스와 같아야 한다 — tests/test_knee_data_profile.py 가 대조한다). paths 는 열람 행의 자체 점검용(등록부에는 넣지 않음).
SERIES_REQUIRED = {"study_idx", "manifest_version", "slice_set_sha256", "slice_sha256", "slice_sizes", "kind", "notice", "version",
                   "registered_by", "registered_at"}
SERIES_OPTIONAL = {"status", "paths"}
REFERENCE_REQUIRED = {"reference_id", "study_idx", "manifest_version", "slice_set_sha256", "slice_no", "image_path", "image_sha256",
                      "plane", "x", "y", "width", "height", "source", "version", "reviewed_by", "reviewed_at"}
REFERENCE_OPTIONAL = {"coord_system", "status", "source_slice_no", "selection_reason", "image_width", "image_height"}

_lock = threading.Lock()
_state: dict | None = None


def content_sha256(targets, registered_rows, reference_rows) -> str:
    """묶음 내용 지문: 대상 번호·열람 행·승인 행의 정규화 JSON 의 SHA-256(메타데이터는 제외)."""
    material = json.dumps({"targets": sorted(targets), "registered_rows": registered_rows, "reference_rows": reference_rows},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _keys_ok(row, required, optional) -> bool:
    return isinstance(row, dict) and required <= set(row) and set(row) <= required | optional


def validate_bundle(body) -> tuple[list, list]:
    """확정 묶음이면 (열람 행, 승인 행), 아니면 ValueError(사유)."""
    if not isinstance(body, dict) or body.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("schema")
    if body.get("status") != "confirmed":
        raise ValueError("not_confirmed")
    if not isinstance(body.get("bundle_version"), str) or not body["bundle_version"].strip():
        raise ValueError("bundle_version")
    targets = body.get("target_study_idx")
    reg, ref = body.get("registered_rows"), body.get("reference_rows")
    if not (isinstance(targets, list) and targets and all(isinstance(t, int) and not isinstance(t, bool) and t > 0 for t in targets)):
        raise ValueError("targets")
    if len(set(targets)) != len(targets):
        raise ValueError("targets_duplicate")
    if not (isinstance(reg, list) and isinstance(ref, list)):
        raise ValueError("rows")
    if not all(_keys_ok(r, SERIES_REQUIRED, SERIES_OPTIONAL) for r in reg) or not all(_keys_ok(r, REFERENCE_REQUIRED, REFERENCE_OPTIONAL) for r in ref):
        raise ValueError("row_fields")
    reg_ids = [r["study_idx"] for r in reg]
    if sorted(reg_ids) != sorted(targets):
        raise ValueError("registered_targets")          # 열람 행은 대상과 정확히 1:1
    ref_studies = [r["study_idx"] for r in ref if r.get("status", "A") == "A"]
    if len(set(ref_studies)) != len(ref_studies) or not set(ref_studies) <= set(targets):
        raise ValueError("reference_targets")           # 같은 study 승인 둘 이상·대상 밖 승인 금지
    ref_ids = [r["reference_id"] for r in ref]
    if len(set(ref_ids)) != len(ref_ids):
        raise ValueError("reference_id_duplicate")
    if body.get("content_sha256") != content_sha256(targets, reg, ref):
        raise ValueError("content_sha256")
    return reg, ref


def _compute() -> dict:
    profile = config.data_profile()
    if profile == "local":
        return {"profile": "local", "ok": True, "detail": "", "registered": None, "reference": None}
    if profile != "nas_presentation":
        return {"profile": profile, "ok": False, "detail": "unknown_profile", "registered": [], "reference": []}
    path = config.presentation_bundle_path()
    try:
        if not path:
            raise ValueError("bundle_path_missing")
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        reg, ref = validate_bundle(body)
    except (OSError, ValueError) as exc:   # json.JSONDecodeError 는 ValueError
        return {"profile": profile, "ok": False, "detail": f"bundle_invalid:{type(exc).__name__}:{exc}"[:120], "registered": [], "reference": []}
    return {"profile": profile, "ok": True, "detail": "", "registered": reg, "reference": ref}


def state() -> dict:
    """선택 결과(프로세스에서 한 번 계산해 고정). registered/reference 가 None 이면 local(기존 데이터 파일 사용)."""
    global _state
    with _lock:
        if _state is None:
            _state = _compute()
        return _state


def reset_for_tests() -> None:
    global _state
    with _lock:
        _state = None
