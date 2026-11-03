"""
화면 캡처 한 장 분석 결과를 만든다 (POST /api/v1/knee/screen-roi 의 계산 부분).

핵심 규칙:
- 그 촬영면에 모델이 있으면 한 장 점수를 낸다. 승인표(gates.APPROVED)에 없는 조합은
  검수된 보정·성능 근거 없이 원래 모델 점수만 학습용으로 보여 준다.
- 그 촬영면에 모델이 없으면 `not_evaluated`(model_plane_unavailable)이고 모델을 부르지 않는다.
- ACL 위치 박스는 승인된 sagittal + full_slice 에서만 나오며 "해부학적 대략 위치"로만 표시한다.
- HTTP 200 은 요청을 처리했다는 뜻이지 진단에 성공했다는 뜻이 아니다.
- 이 파일은 torch 를 불러오지 않는다. 모델이 필요한 세 함수(_predict_single, _gradcam, _locate_acl)만 필요할 때 불러온다.
"""
from __future__ import annotations

import base64
import io
import sys
import time

import numpy as np

from . import gates
from .config import GATING_BAND
from .constants import DISCLAIMER, TASK_KO, UNCERTAIN_VERDICT, VERDICT
from .errors import KneeAnalysisError, KneeModelNotReady
from .explanations import EXPLANATIONS

REASONS = {
    "model_plane_unavailable": "이 촬영면에는 해당 소견을 판단하는 모델이 없어 평가하지 않습니다.",
    "single_image_unvalidated": "한 장으로 이 소견을 판단하는 방식은 검증되지 않았습니다. 학습용 모델 점수만 표시합니다.",
}

OVERLAY_NOTE = "오버레이는 모델이 본 224×224 입력 그림 위에 그려져 있으며, 원본 캡처와 크기와 잘린 범위가 다를 수 있습니다."

RETENTION_NOTE = "서버에서 처리를 마치고 닫은 사실만 말합니다. 브라우저 화면 공유나 외부 뷰어(PACS)에 있는 원본의 삭제를 뜻하지 않습니다."


def _notices(image_scope: str) -> list[dict]:
    items = [
        ("single_capture", "이 분석은 화면 캡처 한 장, 한 촬영면만 사용합니다. 원래 모델은 여러 슬라이스를 함께 보고 판단하도록 만들어져 있어, 한 장만으로는 정확하지 않을 수 있습니다."),
        ("other_findings", "선택한 촬영면에서 판단할 수 없는 소견은 평가하지 않고 '평가하지 않음'으로 표시합니다."),
        ("visualization_meaning", "색으로 표시되는 영역(Grad-CAM)은 모델이 참고한 곳일 뿐 병변의 위치나 윤곽이 아닙니다. ACL 위치 박스는 ACL 이 있는 대략적인 해부학적 위치이며 파열 위치가 아닙니다."),
        ("learning_only", DISCLAIMER + " 이 서비스는 의료 판단 도구가 아니라 의료 판단을 보조하는 학습 도구이며, 자세한 결과는 전문가와 상의해야 합니다."),
        ("deidentify", "전송하기 전에 환자 이름 등 개인정보가 화면에 보이지 않는지 직접 확인해 주세요. 서버는 자동으로 가려 주지 않습니다."),
        ("deletion", "서버는 올린 이미지를 분석 직후 삭제하며 이미지 파일은 저장하지 않습니다. 소견 점수 문구는 학습 기록으로 남을 수 있습니다. 화면 공유 중인 외부 뷰어의 원본은 삭제되지 않습니다."),
    ]
    if image_scope == "roi_crop":
        items.insert(1, ("roi_crop", "선택한 영역만 잘라 보낸 이미지는 전체 슬라이스와 다른 조건이며, 이 조건은 검증되지 않았습니다."))
    return [{"code": c, "text": t} for c, t in items]


# ── 모델이 필요한 부분(테스트에서는 이 세 함수를 바꿔 끼운다) ─────────────────
def _models_loaded() -> bool:
    module = sys.modules.get(f"{__package__}.ensemble")
    return bool(module is not None and module.knee_service.ready)


def _predict_single(finding: str, plane: str, gray: np.ndarray) -> float:
    from .ensemble import knee_service

    return knee_service.single_plane_prob(finding, plane, gray)


def _gradcam(finding: str, plane: str, gray: np.ndarray):
    from .ensemble import knee_service

    return knee_service.single_plane_gradcam(finding, plane, gray)


def _locate_acl(gray: np.ndarray) -> dict | None:
    try:
        from .localization import Localization

        return Localization.locate_acl(gray.astype("float32") / 255.0)
    except (KneeModelNotReady, KneeAnalysisError):
        raise
    except ImportError:
        raise KneeModelNotReady("위치 모델을 준비하지 못했습니다.") from None
    except RuntimeError:
        raise KneeModelNotReady("위치 모델을 준비하지 못했습니다.") from None


# ── 판정 ────────────────────────────────────────────────────────────────
def _logit(p: float) -> float:
    p = float(np.clip(p, 1e-6, 1 - 1e-6))
    return float(np.log(p / (1 - p)))


def _calibrated(raw: float, calibration: tuple[float, float]) -> float:
    a, b = calibration
    value = float(1.0 / (1.0 + np.exp(-(a * _logit(raw) + b))))
    if not np.isfinite(value) or not 0.0 <= value <= 1.0:
        raise KneeAnalysisError("보정된 확률이 정상 범위(0~1)가 아닙니다.")
    return value


def _base_finding(finding: str, plane: str) -> dict:
    return {
        "label_ko": TASK_KO[finding],
        "status": "not_evaluated",
        "reason_code": None,
        "reason": None,
        "prob": None,
        "score_label": None,
        "confidence_band": None,
        "verdict": None,
        "planes_used": [],
        "missing_planes": [p for p in gates.FINDING_PLANES[finding] if p != plane],
    }


def _evaluate(finding: str, plane: str, image_scope: str, gray: np.ndarray):
    """한 소견을 판정한다. 반환: (결과 dict, 승인 기록 또는 None, 보정된 확률 또는 None)."""
    result = _base_finding(finding, plane)

    if not gates.model_plane_available(finding, plane):
        result.update(reason_code="model_plane_unavailable", reason=REASONS["model_plane_unavailable"],
                      missing_planes=list(gates.FINDING_PLANES[finding]))
        return result, None, None

    gate = gates.find_gate(finding, plane, image_scope)
    raw = _predict_single(finding, plane, gray)
    if gate is not None:
        prob = _calibrated(raw, gate.calibration)
        lo, hi = gate.band
        score_label = "모델 점수(임상적 발병 확률이 아님)"
        reason_code, reason = None, None
    else:
        prob = float(raw)
        if not np.isfinite(prob) or not 0.0 <= prob <= 1.0:
            raise KneeAnalysisError("모델 점수가 정상 범위(0~1)가 아닙니다.")
        lo, hi = GATING_BAND[finding]
        score_label = "모델 점수(한 장·미검증, 임상적 발병 확률이 아님)"
        reason_code, reason = "single_image_unvalidated", REASONS["single_image_unvalidated"]
    confident = not (lo <= prob <= hi)
    high_text, low_text = VERDICT[finding]
    verdict = UNCERTAIN_VERDICT if not confident else (high_text if prob >= 0.5 else low_text)
    result.update(
        status="evaluated",
        reason_code=reason_code,
        reason=reason,
        prob=round(prob, 3),
        score_label=score_label,
        confidence_band="confident" if confident else "uncertain",
        verdict=verdict,
        planes_used=[plane],
    )
    return result, gate, prob


def analyze(gray: np.ndarray, *, plane: str, image_scope: str, include_overlay: bool, width: int, height: int,
            request_id: str) -> dict:
    """게이트 표를 지켜 응답 본문(retention 제외)을 만든다. gray: (H, W) uint8."""
    started = time.perf_counter()

    findings: dict[str, dict] = {}
    scored: dict[str, float] = {}
    evaluated: dict[str, tuple] = {}
    for finding in gates.FINDINGS:
        result, gate, prob = _evaluate(finding, plane, image_scope, gray)
        findings[finding] = result
        if result["status"] == "evaluated" and prob is not None:
            scored[finding] = prob
        if gate is not None and prob is not None:
            evaluated[finding] = (gate, prob)

    n_eval = len(scored)
    analysis_status = "not_evaluated" if n_eval == 0 else ("evaluated" if n_eval == len(gates.FINDINGS) else "partially_evaluated")

    visualization = {
        "gradcam_status": "not_evaluated", "gradcam_finding": None, "overlay_png_base64": None, "overlay_note": None,
        "acl_location_status": "not_evaluated", "acl_box": None,
        "acl_box_note": None,
    }
    if scored and include_overlay:
        top = max(scored, key=lambda f: scored[f])
        image, activated = _gradcam(top, plane, gray)
        if activated:
            buf = io.BytesIO()
            image.save(buf, "PNG")
            visualization.update(gradcam_status="shown", gradcam_finding=top,
                                 overlay_png_base64=base64.b64encode(buf.getvalue()).decode("ascii"), overlay_note=OVERLAY_NOTE)
        else:
            visualization.update(gradcam_status="no_activation", gradcam_finding=top)

    location_gate = gates.find_location_gate(plane, image_scope) if (plane == "sagittal" and image_scope == "full_slice") else None
    if location_gate is not None:
        box = _locate_acl(gray)
        if box is None:
            visualization["acl_location_status"] = "not_found"
        else:
            visualization.update(acl_location_status="shown", acl_box=box,
                                 acl_box_note="ACL 이 있는 대략적인 해부학적 위치이며 파열 위치가 아닙니다.")

    validated = [
        {"finding": f, "validation_id": g.validation_id, "validated_condition": g.validated_condition,
         "model_id": g.model_id, "performance": g.performance}
        for f, (g, _p) in evaluated.items()
    ]
    if location_gate is not None and visualization["acl_location_status"] != "not_evaluated":
        validated.append({"finding": "acl_location", "validation_id": location_gate.validation_id,
                          "validated_condition": location_gate.validated_condition,
                          "model_id": location_gate.model_id, "performance": location_gate.performance})

    explanations = []
    for f, (g, _p) in evaluated.items():
        entry = EXPLANATIONS[g.explanation_id]
        explanations.append({"finding": f, "id": g.explanation_id, "version": entry.get("version", ""), "text": entry.get("text", "")})

    return {
        "request_id": request_id,
        "body_part": "knee",
        "image": {"width": width, "height": height, "plane": plane, "image_scope": image_scope, "source": "screen_capture"},
        "analysis_status": analysis_status,
        "findings": findings,
        "visualization": visualization,
        "notices": _notices(image_scope),
        "evidence": {"gates_version": gates.GATES_VERSION, "validated": validated},
        "explanations": explanations,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "models_loaded": _models_loaded(),
    }
