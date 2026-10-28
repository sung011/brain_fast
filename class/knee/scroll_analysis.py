"""
무릎 화면 분석(넘기며 분석 / 한 장) 요청 검증·모델 선택·응답 만들기 — MSN-20261021-001 v2 설계 3·5·6장.

라우터(routers/kneeScreenAnalysis.py)는 얇게 두고, 여기서 입력 계약을 검사한다.
- 장 수는 **서버가 디코딩하고 화소 중복을 뺀 수**로 정한다. 클라이언트가 보낸 모델/점수/승인 값은 받지 않는다(모르는 필드 거절).
- scroll: 고유 10~20장 → 기존 ACL 볼륨 모델. 9장 이하 = 수집 부족(422), 21장 이상 = 413.
- single: 정확히 1장 → 한 장 2D 모델(class/knee/single_image.py, 사용자 Colab 반입본 v1). 설정이 없거나 불러오지 못하면 503 model_single_not_ready.
  볼륨 모델로 몰래 대신하지 않는다. scroll 이 중복 제거 뒤 1장이면 부족(422)이지 한 장 모델로 바꾸지 않는다.
- 보정 프로필이 없으면 raw 점수를 그대로 보여 준다(score_kind='raw', decision='score_only'). 0점·정상 판정으로 바꾸지 않는다.
- 기존 gates.APPROVED(한 장 엄격 게이트)는 건드리지 않는다. 이 경로는 사용자 승인 learning_demo 프로필(아래 DEMO_PROFILE)이다.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math

import numpy as np

SCHEMA_VERSION = 1
MIN_SCROLL, RECOMMEND_SCROLL, MAX_FRAMES = 10, 15, 20
MAX_FRAME_BYTES = 1 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MIN_SIDE, MAX_SIDE = 64, 512
PLANES = ("sagittal",)
SCOPES = ("full_slice", "roi_crop")
INTENTS = ("single", "scroll")
MANIFEST_KEYS = {"schema_version", "plane", "image_scope", "capture_intent", "capture_profile_id", "frames"}
FRAME_KEYS = {"sequence", "file_index", "captured_at_ms"}

# 사용자 승인(2026-10-21) 학습용 시연 프로필. 임상/독립 검증 게이트가 아니다.
DEMO_PROFILE = {
    "profile_id": "knee_scroll_demo_v1",
    "usage_mode": "learning_demo",
    "evaluation_class": "exploratory",
    "validation_level": "exploratory_demo",
    "independent_test": False,
    "exploration_report": "designs/MSN-20261021-001/offline_runs/multislice_20261021 (KneeMRI 907, 기존 모델 학습·선정 노출 = 비독립)",
    "reviewed_as": "team_technical",
    "calibration": None,          # 여러 장용 보정은 아직 없음 → raw 표시
}


# 한 장 2D 모델(v1) 시연 프로필. SINGLE_2D_CONNECTION_PLAN 2·3장. raw 점수만 표시(보정·판단 구간·자동 판정 미사용).
SINGLE_PROFILE = {
    "profile_id": "knee_single_demo_v1",
    "usage_mode": "learning_demo",
    "evaluation_class": "exploratory",
    "validation_level": "internal_group_holdout",
    "independent_test": False,
    "reviewed_as": "team_technical",
    "score_policy": "raw_only",
    "label_map": {"0": "negative", "1": "positive", "2": "positive"},
    "plane": "sagittal",
    "scopes": ["full_slice", "roi_crop"],
    "internal_holdout": {
        "groups": 181, "positive_groups": 45, "selection": "dataset_roi_middle_slice",
        "full_slice_auroc": 0.9011, "full_slice_auroc_ci95": [0.8457, 0.9489], "crop_auroc": 0.8850,
        "note": "검사 그룹 단위 내부 holdout 의 AUROC(정확도 아님). 데이터셋 ROI 중간 장(ACL 이 보일 가능성이 높은 장을 미리 아는 조건)이며, "
                "임의로 고른 장·인터넷 영상·화면 캡처 성능이 아닙니다.",
    },
    "label_note": "검사 단위 약한 라벨(원 등급 0 → 음성, 1·2 → 양성). 장마다 파열이 보인다는 정답이 아닙니다.",
    "external": "skipped (MRNet 외부 확인 미실시)",
    "project_history": "같은 원본(KneeMRI)이 이전 프로젝트 볼륨 모델 학습·선정에 쓰인 이력이 있음(이 2D 모델의 test 그룹은 학습·보정에 쓰지 않음)",
    "operating_point": {"high_side_error_observed": "8/38 = 0.2105", "target": "<= 0.20", "met": False, "used_for_decision": False},
    "metric_corrections": "v1 원 보고서의 AUPRC·crop 보정 지표는 별도 정정 기록(v1_correction_01: 같은 test 의 감사 재생 1회 = 새 추론, 재학습·독립 검증 아님)에 있음. 원 v1 보고서는 그대로 보존. 위 AUROC 는 원 v1 보고 값.",
    "model_report_sha256": "1dd489d06d1dd2d979ee25e196381aaf38e91796ac07be0ce91fdccdb2249ab6",
}


class ScreenAnalysisError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def _bad(status, code, message):
    raise ScreenAnalysisError(status, code, message)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def parse_manifest(text: str, n_files: int) -> dict:
    """manifest JSON 엄격 검사. 파일 수·인덱스·순번·시각이 정확히 맞아야 한다."""
    if not isinstance(text, str) or len(text) > 64 * 1024:
        _bad(400, "invalid_manifest", "manifest 형식이 올바르지 않습니다.")
    try:
        def no_dupes(pairs):
            keys = [k for k, _ in pairs]
            if len(keys) != len(set(keys)):
                raise ValueError("dup")
            return dict(pairs)
        m = json.loads(text, object_pairs_hook=no_dupes, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except (ValueError, TypeError):
        _bad(400, "invalid_manifest", "manifest 를 읽을 수 없습니다(JSON·중복 키·NaN 금지).")
    if not isinstance(m, dict) or set(m) != MANIFEST_KEYS:
        _bad(400, "invalid_manifest", "manifest 필드가 올바르지 않습니다(허용 필드만).")
    if not _int(m["schema_version"]) or m["schema_version"] != SCHEMA_VERSION:   # true(=1) 같은 bool 은 거절
        _bad(400, "unsupported_schema", "지원하지 않는 manifest 버전입니다.")
    if m["plane"] not in PLANES:
        _bad(400, "unsupported_plane", "지금은 시상면(sagittal)만 분석합니다.")
    if m["image_scope"] not in SCOPES or m["capture_intent"] not in INTENTS:
        _bad(400, "invalid_manifest", "image_scope/capture_intent 값이 올바르지 않습니다.")
    if not isinstance(m["capture_profile_id"], str) or not (0 < len(m["capture_profile_id"]) <= 64):
        _bad(400, "invalid_manifest", "capture_profile_id 가 올바르지 않습니다.")
    frames = m["frames"]
    if not isinstance(frames, list) or len(frames) != n_files:
        _bad(400, "invalid_manifest", "manifest 의 장 수와 올린 파일 수가 다릅니다.")
    seqs, idxs, last, last_t = [], [], -1, -1
    for f in frames:
        if not isinstance(f, dict) or set(f) != FRAME_KEYS or not all(_int(f[k]) for k in FRAME_KEYS):
            _bad(400, "invalid_manifest", "frames 항목 형식이 올바르지 않습니다.")
        if f["captured_at_ms"] < 0 or f["sequence"] <= last or f["captured_at_ms"] < last_t:
            _bad(400, "invalid_manifest", "sequence 는 증가, 시각은 0 이상이며 줄어들지 않아야 합니다.")
        last, last_t = f["sequence"], f["captured_at_ms"]
        seqs.append(f["sequence"])
        idxs.append(f["file_index"])
    if sorted(idxs) != list(range(n_files)):
        _bad(400, "invalid_manifest", "file_index 가 파일 목록과 정확히 대응하지 않습니다.")
    return m


def decode_frames(blobs: list[bytes], manifest: dict):
    """바이트로 형식 확인 → 회색 디코드 → 크기 검사 → 원 화소 해시로 정확 중복 제거. 반환: (고유 장 목록[(sequence, gray)], 받은 수)."""
    from .imageio import ImageRejected, decode_screen_image

    if not blobs:
        _bad(422, "no_frames", "분석할 장이 없습니다.")
    if len(blobs) > MAX_FRAMES:
        _bad(413, "too_many_frames", f"한 번에 최대 {MAX_FRAMES}장까지 분석합니다.")
    if sum(len(b) for b in blobs) > MAX_TOTAL_BYTES or any(len(b) > MAX_FRAME_BYTES for b in blobs):
        _bad(413, "payload_too_large", "장별 1MiB·전체 16MiB 를 넘었습니다.")
    order = {f["file_index"]: f for f in manifest["frames"]}
    decoded, size = [], None
    for i, data in enumerate(blobs):
        try:
            d = decode_screen_image(data)
        except ImageRejected as exc:
            raise ScreenAnalysisError(exc.status, exc.code, exc.message) from None
        if not (MIN_SIDE <= d.width <= MAX_SIDE and MIN_SIDE <= d.height <= MAX_SIDE):
            _bad(422, "frame_size", f"각 장은 한 변 {MIN_SIDE}~{MAX_SIDE}px 이어야 합니다.")
        if size is None:
            size = (d.width, d.height)
        elif (d.width, d.height) != size:
            _bad(422, "mixed_frame_size", "모든 장의 크기가 같아야 합니다(박스를 바꾸지 마세요).")
        decoded.append((order[i]["sequence"], d.gray))
    decoded.sort(key=lambda t: t[0])
    seen, unique = set(), []
    for seq, gray in decoded:
        h = hashlib.sha256(np.ascontiguousarray(gray).tobytes()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        unique.append((seq, gray))
    return unique, len(blobs), size


def choose_model(intent: str, unique_count: int) -> str:
    if intent == "scroll":
        if unique_count < MIN_SCROLL:
            _bad(422, "insufficient_frames", f"서로 다른 장이 {unique_count}장입니다. 최소 {MIN_SCROLL}장(권장 {RECOMMEND_SCROLL}장)을 모아 주세요.")
        if unique_count > MAX_FRAMES:
            _bad(413, "too_many_frames", f"최대 {MAX_FRAMES}장까지 분석합니다.")
        return "volume"
    if unique_count != 1:
        _bad(422, "single_needs_one", "한 장 분석은 정확히 1장이어야 합니다. 여러 장은 '넘기며 분석'을 사용해 주세요.")
    return "single_2d"


def score_text(p: float) -> str:
    return f"{p * 100:.1f}/100"


DIAG_NOTE = "데이터셋 라벨 기준 참고 자료이며 임상 검증을 거치지 않았고 의료 진단이 아닙니다."
BAND_TEXT = {"high": "데이터셋 손상 라벨 쪽 참고구간", "low": "낮은 참고구간", "uncertain": "두 라벨을 구분하기 어려운 구간"}


BOX_TITLE = "ACL 대략적 위치 안내"
BOX_LEGEND = "ACL이 포함될 수 있는 참고 영역 — 병변 위치 아님"
BOX_RELATION = "점수는 여러 장 분류 모델, 박스는 별도 ACL 위치 모델의 결과입니다. 박스가 점수의 원인이나 손상 위치를 뜻하지 않습니다."
BOX_NOTE = "ACL 대략적 위치 안내 · 병변 위치나 분류 모델의 판단 근거가 아닙니다."
LOC_WITHHELD = {   # CAM_LOCALIZATION_STEP2 4장 표 — 위치만 보류, 점수는 그대로
    "disabled_by_policy": "ACL 위치 안내를 꺼 두었습니다. 분석 점수와 해설은 표시합니다.",
    "not_found": "이 장에서 ACL 위치를 표시하지 못했습니다. 정상이라는 뜻은 아닙니다.",
    "unavailable": "위치 안내를 준비하지 못했습니다. 분석 점수는 표시합니다.",
    "unsupported_scope": "영상 일부 입력이라 ACL 위치 안내를 생략했습니다.",
    "invalid_box": "위치 박스를 표시하지 못했습니다.",
}
CAM_TITLE = "모델 참고 열지도(Grad-CAM)"
CAM_NOTE = ("모델 점수에 대한 참고 반응이며 병변 위치·윤곽이 아닙니다. 정답 ROI로 고른 대표 장에서의 측정 결과를 임의 영상에 보장하지 않습니다. "
            "그림은 모델 입력(224×224, 원래 종횡비와 다를 수 있음) 위에 그렸습니다.")


def _location_sentence(used: int, loc: dict) -> str:
    st = loc.get("status")
    if st == "not_requested":
        return ""
    base = "여러 장 모델의 열지도는 ACL 위치와 일치가 낮아 이번 표시에서 제외했습니다."
    if st == "shown":
        return (f"{base} 위치 박스는 분석에 쓴 {used}장 중 {loc['frame_ordinal']}번째 장(수집 순번 {loc['frame_sequence']})에서 별도 ACL 위치 모델이 찾은 대략적 위치이며, "
                "학습 자료에 노출된 모델의 참고 결과로 화면 캡처에서의 정확도는 확인되지 않았습니다. " + BOX_RELATION)
    return f"{base} {LOC_WITHHELD.get(st, LOC_WITHHELD['unavailable'])}"


def explanation_template(raw: float, used: int, scope: str, loc: dict, ref: dict | None = None) -> str:
    # 0.5 같은 임의 기준으로 방향을 나누지 않는다(CALIBRATION_PRIORITY_DESIGN 6장). 서버가 고른 참고선이 있을 때만 위/아래를 말한다.
    if ref and ref.get("applied"):
        lean = (f"학습에 사용된 자료에서 정한 참고선({ref['threshold_raw'] * 100:.1f})보다 {'위' if ref['position'] == 'above' else '아래'}입니다. "
                "다른 영상에서의 성능은 확인하지 않았습니다")
    else:
        lean = "참고선 없이 점수만 표시합니다"
    few = f" 권장 {RECOMMEND_SCROLL}장보다 적은 {used}장으로 분석해 점수가 덜 안정적일 수 있습니다." if used < RECOMMEND_SCROLL else ""
    where = _location_sentence(used, loc)
    roi = " 선택 영역이 영상 일부(roi_crop)라 전체 장 기준과 조건이 다릅니다." if scope == "roi_crop" else ""
    return (f"선택 영역에서 수집한 {used}장을 ACL 시상면 모델에 한꺼번에 넣었을 때 모델 점수는 {score_text(raw)}(보정 전)이며, {lean}. "
            f"이 점수는 실제 질병 확률이나 진단 정확도가 아니며 정상·파열을 판정하지 않습니다.{few}{roi}{(' ' + where) if where else ''} "
            "모델은 KneeMRI·MRNet 자료로 학습됐고 이 시연 자료는 학습·선정에 노출돼 독립 검증이 아닙니다. " + DIAG_NOTE + " 의료 판단은 전문가와 상의해야 합니다.")


def _volume_visualization(loc: dict) -> dict:
    """넘기며 분석 visualization. 볼륨 CAM 은 쓰지 않는다(평가 후 숨긴 것이 아니라 계산하지 않음 → no_activation 으로 표시하지 않음)."""
    loc = dict(loc)
    png = loc.pop("_png", None)
    st = loc.get("status")
    if st == "shown" and png:
        return {"status": "shown", "kind": "anatomical_box", "overlay_png_base64": png, "overlay_frame_sequence": loc["frame_sequence"],
                "model_input_size": 224, "note": BOX_NOTE, "title": BOX_TITLE, "legend": BOX_LEGEND, "relation": BOX_RELATION, "localization": loc}
    if st == "not_requested":
        return {"status": "not_requested", "kind": "none", "overlay_png_base64": None, "overlay_frame_sequence": None, "model_input_size": 224,
                "note": None, "localization": loc}
    return {"status": "withheld", "kind": "none", "overlay_png_base64": None, "overlay_frame_sequence": None, "model_input_size": 224,
            "note": LOC_WITHHELD.get(st, LOC_WITHHELD["unavailable"]), "relation": BOX_RELATION, "localization": loc}


def single_explanation(raw: float, scope: str, activated: bool, ref: dict | None = None) -> str:
    # 0.5 분기 제거. 서버 sidecar 의 비교선(τ)·참고구간이 적용될 때만 위치를 말한다.
    if ref and ref.get("applied"):
        lean = (f"데이터셋 기준 비교선({ref['threshold_raw'] * 100:.1f}, calibration 자료로 정하고 이미 사용한 내부 시험 자료로 확인)보다 "
                f"{'높은' if ref['position'] == 'above' else '낮은'} 쪽입니다")
        if ref.get("band"):
            lean += f"({BAND_TEXT[ref['band']]})"
    else:
        lean = "비교 기준선 없이 점수만 표시합니다"
    cam = ("모델 참고 열지도(Grad-CAM)는 모델 점수에 대한 참고 반응을 이 한 장(모델 입력 224×224) 위에 표시한 것이며 병변 위치·윤곽이 아닙니다. "
           "정답 ROI로 고른 대표 장에서 잰 위치 일치를 임의로 고른 장·화면 캡처에 보장하지 않습니다.") if activated else \
          "이번 입력에서는 참고 열지도로 표시할 만한 뚜렷한 반응이 없었습니다(점수는 그대로 계산됨). 색칠이 없다는 것이 정상이라는 뜻은 아닙니다."
    roi = " 선택 영역이 영상 일부(roi_crop)라 영상 한 장 전체 기준과 조건이 다르며, 내부 시험에서도 이 조건의 구분력이 더 낮았습니다." if scope == "roi_crop" else ""
    return (f"선택한 한 장을 ACL 시상면 한 장 모델(2D)에 넣었을 때 모델 점수는 {score_text(raw)}(보정 전)이며, {lean}. "
            f"이 점수는 실제 질병 확률이나 진단 정확도가 아니며 정상·파열을 판정하지 않습니다(낮은 쪽도 '정상'이라는 뜻이 아님).{roi} {cam} "
            "비교선은 데이터셋 시리즈의 가운데 장 조건에서 정했으므로, 다른 장을 고르거나 화면을 캡처한 경우에는 그 조건이라고 가정한 참고입니다. "
            "검사 단위 라벨로 학습한 학습용 참고 자료입니다. " + DIAG_NOTE + " 의료 판단은 전문가와 상의해야 합니다.")


def build_response(*, request_id: str, manifest: dict, received: int, unique: list, size, model, result: dict, latency_ms: int, device: str,
                   kind: str = "volume") -> dict:
    """kind='volume'(넘기며 분석, 기존 응답 그대로) 또는 'single_2d'(한 장 모델: 같은 schema, 프로필·해설·근거만 다름)."""
    if kind == "single_2d":
        return _build_single(request_id=request_id, manifest=manifest, received=received, unique=unique, size=size, model=model,
                             result=result, latency_ms=latency_ms, device=device)
    raw = result["raw_score"]
    used = len(unique)
    loc = result.get("localization") or {"status": "not_requested"}   # 볼륨 CAM(result 의 overlay·cam_index)은 쓰지 않는다
    visualization = _volume_visualization(loc)
    warnings = []
    if used < RECOMMEND_SCROLL:
        warnings.append({"code": "below_recommended", "text": f"권장 {RECOMMEND_SCROLL}장보다 적은 {used}장으로 분석했습니다."})
    if received != used:
        warnings.append({"code": "duplicates_removed", "text": f"같은 화면 {received - used}장을 빼고 {used}장을 사용했습니다."})
    from .score_reference import volume_reference

    # 일반 화면 공유 요청에는 '가운데 연속 15장' 을 서버가 확인할 근거가 없다 → condition_verified=False(참고선만 생략, raw·CAM·해설 유지)
    ref = volume_reference(model_sha256=model.model_sha256, scope=manifest["image_scope"], used_count=used, raw=raw, condition_verified=False)
    vref = None
    if ref["applied"]:
        vref = {"label": "학습 노출 자료 기준선(탐색)", "threshold": ref["threshold_raw"], "position": ref["position"], "version": ref["doc"]["version"],
                "input_condition": "assumed_center_15_unverified",
                "text": f"학습에 사용된 자료에서 정한 참고선({ref['threshold_raw'] * 100:.1f})보다 {'위' if ref['position'] == 'above' else '아래'}입니다. 다른 영상에서의 성능은 확인하지 않았습니다."}
    return {
        "schema_version": SCHEMA_VERSION, "request_id": request_id, "body_part": "knee", "analysis_status": "evaluated",
        "usage_mode": DEMO_PROFILE["usage_mode"], "evaluation_class": DEMO_PROFILE["evaluation_class"], "profile_id": DEMO_PROFILE["profile_id"],
        "model_kind": "volume", "model_id": model.model_id, "model_sha256": model.model_sha256,
        "capture": {"received_count": received, "unique_count": used, "used_count": used, "scope": manifest["image_scope"], "plane": manifest["plane"],
                    "profile_id": manifest["capture_profile_id"], "frame_width": size[0], "frame_height": size[1],
                    "source_assurance": "user_declared_same_exam", "order_assurance": "capture_time_only"},
        "acl": {"label_ko": "ACL 데이터셋 라벨 비교 참고 점수", "raw_score": round(raw, 6), "calibrated_score": None, "display_score": round(raw, 6),
                "score_kind": "raw", "calibration_id": None, "decision": "score_only", "band": None,
                "display_text": f"{score_text(raw)} (보정 전 · 학습용 참고 점수)",
                "reference": vref, "reference_status": None if vref else ref["reason"]},
        "visualization": visualization,
        "explanations": [{"finding": "acl", "title": "ACL 모델 점수 해설", "text": explanation_template(raw, used, manifest["image_scope"], loc, ref),
                          "source": "model_result_template", "reviewed_as": DEMO_PROFILE["reviewed_as"], "clinical_review": False}],
        "evidence": {"validation_level": DEMO_PROFILE["validation_level"], "independent_test": DEMO_PROFILE["independent_test"],
                     "exploration_report": DEMO_PROFILE["exploration_report"], "calibration_profile": None,
                     "reference_profile": ({"version": ref["doc"]["version"], "exposure": ref["doc"]["evidence"]["exposure"], "independent_test": False}
                                           if ref["applied"] else None)},
        "notices": [{"code": "learning_demo", "text": "학습용 시연 점수입니다. 의료 진단이 아니며 임상·독립 검증을 거치지 않았습니다."},
                    {"code": "scope", "text": "ACL 외 소견(반월판·전체 정상 여부)과 부분/완전 파열 구분은 이번 분석 범위가 아닙니다."},
                    {"code": "deidentify", "text": "전송 전에 화면에 환자 정보가 보이지 않는지 직접 확인해 주세요. 서버는 분석 직후 이미지를 지우고 저장하지 않습니다."}],
        "warnings": warnings, "latency_ms": latency_ms, "device": device,
        "retention": {"input_deleted": True, "transient_only": True},
    }


def _overlay(result):
    if result.get("overlay") is None:
        return None, "not_requested"
    if not result.get("activated"):
        return None, "no_activation"
    buf = io.BytesIO()
    result["overlay"].save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii"), "shown"


def _build_single(*, request_id, manifest, received, unique, size, model, result, latency_ms, device) -> dict:
    p = SINGLE_PROFILE
    raw = result["raw_score"]
    overlay_b64, vis_status = _overlay(result)
    scope = manifest["image_scope"]
    from .score_reference import single_reference

    ref = single_reference(model_sha256=model.model_sha256, scope=scope, plane=manifest["plane"], raw=raw, logit=result.get("logit", _logit(raw)))
    calibrated = ref["calibrated"]
    sref = None
    if ref["applied"]:
        doc = ref["doc"]
        thr = ref["threshold_raw"] if calibrated is None else 1.0 / (1.0 + __import__("math").exp(-(doc["calibration"]["a"] * _logit(ref["threshold_raw"]) + doc["calibration"]["b"])))
        sref = {"label": "데이터셋 기준 비교선", "threshold": round(thr, 6), "position": ref["position"], "version": doc["version"],
                "evidence_status": "calibration_fixed_legacy_test_audited", "input_condition": "assumed_dataset_mid_slice",
                "band_text": BAND_TEXT[ref["band"]] if ref["band"] else None}
    warnings = []
    if scope == "roi_crop":
        warnings.append({"code": "scope_roi_crop", "text": "영상 일부(roi_crop) 조건: 내부 시험에서 영상 한 장 전체보다 구분력이 낮았습니다(AUROC 0.885 대 0.901, 정확도 아님)."})
    return {
        "schema_version": SCHEMA_VERSION, "request_id": request_id, "body_part": "knee", "analysis_status": "evaluated",
        "usage_mode": p["usage_mode"], "evaluation_class": p["evaluation_class"], "profile_id": p["profile_id"],
        "model_kind": "single_2d", "model_id": model.model_id, "model_sha256": model.model_sha256,
        "capture": {"received_count": received, "unique_count": len(unique), "used_count": len(unique), "scope": scope, "plane": manifest["plane"],
                    "profile_id": manifest["capture_profile_id"], "frame_width": size[0], "frame_height": size[1],
                    "source_assurance": "user_selected_single_capture", "order_assurance": "not_applicable"},
        "acl": {"label_ko": "ACL 데이터셋 라벨 비교 참고 점수", "raw_score": round(raw, 6),
                "calibrated_score": round(calibrated, 6) if calibrated is not None else None,
                "display_score": round(calibrated if calibrated is not None else raw, 6),
                "score_kind": "calibrated" if calibrated is not None else "raw",
                "calibration_id": ref["doc"]["version"] if ref["applied"] else None,
                "decision": "reference_compared" if ref["applied"] else "score_only", "band": ref["band"],
                "display_text": (f"{score_text(calibrated)} (내부 자료 보정 참고값)" if calibrated is not None
                                 else f"{score_text(raw)} (보정 전 · 학습용 참고 점수)"),
                "reference": sref, "reference_status": None if ref["applied"] else ref["reason"]},
        "visualization": {"status": vis_status, "kind": "grad_cam" if vis_status in ("shown", "no_activation") else "none",
                          "overlay_png_base64": overlay_b64,
                          "overlay_frame_sequence": unique[0][0] if result.get("cam_index") is not None else None,
                          "model_input_size": 224, "title": CAM_TITLE,
                          "note": CAM_NOTE, "localization": None},
        "explanations": [{"finding": "acl", "title": "ACL 한 장 모델 참고 점수 해설", "text": single_explanation(raw, scope, vis_status == "shown", ref),
                          "source": "model_result_template", "reviewed_as": p["reviewed_as"], "clinical_review": False}],
        "evidence": {"validation_level": p["validation_level"], "independent_test": p["independent_test"], "score_policy": p["score_policy"],
                     "internal_holdout": p["internal_holdout"], "label": p["label_note"], "label_map": p["label_map"], "external": p["external"],
                     "project_history": p["project_history"], "operating_point": p["operating_point"],
                     "calibration_profile": ({"version": ref["doc"]["version"], "input_policy": ref["doc"]["input_policy"],
                                              "legacy_test_reuse": True, "fresh_test": False, "independent_test": False,
                                              "calibration": ref["doc"]["evidence"]["cal"], "audit": ref["doc"]["evidence"]["audit"],
                                              "calibrated_display_adopted": bool(ref["doc"]["calibration"]["adopted"]),
                                              "legacy_operating_point_note": "기존 8/38=0.211(ROI 가운데 장 band)은 이력으로만 유지, 새 입력에 상속 안 함"}
                                             if ref["applied"] else None),
                     "metric_corrections": p["metric_corrections"], "model_report_sha256": p["model_report_sha256"]},
        "notices": [{"code": "learning_demo", "text": "학습용 시연 점수입니다. 의료 진단이 아니며 임상·외부 검증을 거치지 않았습니다."},
                    {"code": "single_selection", "text": "이 한 장 모델과 비교선은 데이터셋 시리즈의 가운데 장 조건으로만 정하고 확인했습니다. 임의로 고른 장·인터넷 영상·화면 캡처에서의 성능은 확인되지 않았습니다(서버는 입력이 그 조건인지 확인하지 못함)."},
                    {"code": "scope", "text": "ACL 외 소견(반월판·전체 정상 여부)과 부분/완전 파열 구분은 이번 분석 범위가 아닙니다."},
                    {"code": "deidentify", "text": "전송 전에 화면에 환자 정보가 보이지 않는지 직접 확인해 주세요. 서버는 분석 직후 이미지를 지우고 저장하지 않습니다."}],
        "warnings": warnings, "latency_ms": latency_ms, "device": device,
        "retention": {"input_deleted": True, "transient_only": True},
    }


def _logit(p: float) -> float:
    import math
    p = min(max(float(p), 1e-12), 1 - 1e-12)
    return math.log(p / (1 - p))
