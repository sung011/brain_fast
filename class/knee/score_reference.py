"""
무릎 화면 분석 점수의 보정·비교 기준선 sidecar(버전 파일) 읽기 — MSN-20261021-001 CALIBRATION_PRIORITY_DESIGN 4·5·6장.

- 모델 파일(single_image.BUNDLE_FILES)은 그대로 두고, 보정/기준선은 **별도 sidecar JSON** 으로 둔다. 서버 설정으로만 경로를 받는다
  (KNEE_SINGLE_CALIBRATION_FILE, KNEE_VOLUME_REFERENCE_FILE). 요청에서 경로·계수·기준선을 받지 않는다.
- 읽은 바이트의 SHA-256 이 코드에 고정한 값과 같고, 형식·모델 SHA·입력 정책·수치 범위가 맞을 때만 쓴다. 하나라도 어긋나면
  쓰지 않고(None) 사유 코드를 돌려준다 → 응답은 raw 점수만(score_only) + 사유. 오류를 0점이나 정상 판정으로 바꾸지 않는다.
- 판단은 서버에서만 한다(클라이언트는 보정·판정 계산을 하지 않는다).
- 한 장 v2: calibration 181 로 고정한 뒤 과거 사용 test 181 을 버전별 감사 1회 한 결과(새 독립 시험 아님). 감사에서 보정 표시는 미채택 → raw 표시,
  비교 기준선 τ 와 참고 구간(band) 은 채택. 적용 범위: 시상면·영상 한 장 전체(full_slice)·한 장 분석. roi_crop 등은 raw 만.
- 볼륨 V15: 학습에 노출된 자료에서 정한 **탐색** 참고선(가운데 연속 15장 조건). 장 수가 15라도 그 장들이 같은 검사의 가운데 연속 15장인지
  서버가 확인한 경우(condition_verified=True, 서버 쪽 통제 시연 맥락)에만 쓴다. 일반 화면 공유는 확인할 수 없으므로 참고선만 생략하고
  raw 점수·히트맵·해설은 그대로 둔다(Codex CALIB-REVIEW-003). 클라이언트가 보낸 '확인됨' 값은 받지 않는다. 3단계 구간 없음.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from pathlib import Path

PINNED = {
    "single": {"sha256": "72bdb38d2d4137d85f2a2a527480f419fe512679db92b499f7e179265c090ef9", "version": "knee_single_calib_v2",
               "kind": "knee_single_calibration_sidecar", "setting": "KNEE_SINGLE_CALIBRATION_FILE",
               "model_sha256": "a34193b41fd0a902bd46fa47cc4dbed0e2a454f275e422474075727afd925199", "input_policy": "whole_volume_mid_full_v2"},
    "volume": {"sha256": "c6ef5fdf0274ee49ff2ba82f13572b35ddcdbd53781175fbe115863087b63341", "version": "knee_volume_ref_v15_exploratory",
               "kind": "knee_volume_reference_sidecar", "setting": "KNEE_VOLUME_REFERENCE_FILE",
               "model_sha256": "bb81d22bf4ee99ab1b74a004ace4167dddf5e4ac87b160f5f23dec9570477393"},
}
MAX_BYTES = 64 * 1024
_cache: dict = {}
_lock = threading.Lock()


def _unit(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0.0 < v < 1.0


def _validate(kind: str, doc: dict) -> str | None:
    pin = PINNED[kind]
    if doc.get("kind") != pin["kind"] or doc.get("version") != pin["version"] or doc.get("model_sha256") != pin["model_sha256"]:
        return "sidecar_mismatch"
    tau = doc.get("reference_threshold_raw")
    if tau is not None and not _unit(tau):
        return "sidecar_invalid"
    if kind == "single":
        if doc.get("input_policy") != pin["input_policy"]:
            return "sidecar_mismatch"
        cal = doc.get("calibration") or {}
        if cal.get("adopted"):
            a, b = cal.get("a"), cal.get("b")
            if not (isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isfinite(a) and math.isfinite(b) and a > 0):
                return "sidecar_invalid"
        band = doc.get("band_raw")
        if band is not None:
            if not (isinstance(band, list) and len(band) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in band)
                    and tau is not None and band[0] <= tau <= band[1]):
                return "sidecar_invalid"
    return None


def load(kind: str, setting_value: str | None):
    """반환 (doc | None, 사유 코드 | None). 같은 경로·같은 내용이면 캐시."""
    if not setting_value:
        return None, "not_configured"
    p = Path(setting_value)
    with _lock:
        try:
            if not p.is_file() or p.is_symlink() or p.stat().st_size > MAX_BYTES:
                return None, "sidecar_missing"
            data = p.read_bytes()
        except OSError:
            return None, "sidecar_missing"
        key = (kind, str(p), hashlib.sha256(data).hexdigest())
        if key in _cache:
            return _cache[key]
        if key[2] != PINNED[kind]["sha256"]:
            out = (None, "sidecar_sha_mismatch")
        else:
            try:
                doc = json.loads(data)
                why = _validate(kind, doc) if isinstance(doc, dict) else "sidecar_invalid"
            except ValueError:
                doc, why = None, "sidecar_invalid"
            out = (None, why) if why else (doc, None)
        _cache[key] = out
        return out


def _setting(name: str) -> str:
    from .config import get_setting

    return get_setting(name, "")


def single_reference(*, model_sha256: str, scope: str, plane: str, raw: float, logit: float) -> dict:
    """한 장 응답용. 반환 {"applied": bool, "reason", "calibrated", "threshold_raw", "position", "band", "doc"}."""
    doc, why = load("single", _setting(PINNED["single"]["setting"]))
    base = {"applied": False, "reason": why, "calibrated": None, "threshold_raw": None, "position": None, "band": None, "doc": doc}
    if doc is None:
        return base
    if model_sha256 != doc["model_sha256"]:
        return {**base, "reason": "model_mismatch", "doc": None}
    ap = doc.get("applies_to") or {}
    if plane != ap.get("plane") or scope not in (ap.get("image_scope") or []):
        return {**base, "reason": "scope_not_supported"}
    tau = doc.get("reference_threshold_raw")
    if tau is None:
        return {**base, "reason": "no_reference_threshold"}
    cal = doc.get("calibration") or {}
    calibrated = None
    if cal.get("adopted"):
        calibrated = 1.0 / (1.0 + math.exp(-(cal["a"] * logit + cal["b"])))
    band = None
    if doc.get("band_raw"):
        l, h = doc["band_raw"]
        band = "low" if raw < l else "high" if raw > h else "uncertain"
    return {"applied": True, "reason": None, "calibrated": calibrated, "threshold_raw": tau,
            "position": "above" if raw >= tau else "below", "band": band, "doc": doc}


def volume_reference(*, model_sha256: str, scope: str, used_count: int, raw: float, condition_verified: bool = False) -> dict:
    doc, why = load("volume", _setting(PINNED["volume"]["setting"]))
    base = {"applied": False, "reason": why, "threshold_raw": None, "position": None, "doc": doc}
    if doc is None:
        return base
    if condition_verified is not True:   # 가운데 연속 15장·같은 검사·전체 범위를 서버가 확인하지 못함 → 참고선 생략
        return {**base, "reason": "condition_unverified"}
    if model_sha256 != doc["model_sha256"]:
        return {**base, "reason": "model_mismatch", "doc": None}
    ap = doc.get("applies_to") or {}
    if used_count != ap.get("used_count") or scope not in (ap.get("image_scope") or []):
        return {**base, "reason": "condition_not_supported"}
    tau = doc.get("reference_threshold_raw")
    if tau is None:
        return {**base, "reason": "no_reference_threshold"}
    return {"applied": True, "reason": None, "threshold_raw": tau, "position": "above" if raw >= tau else "below", "doc": doc}
