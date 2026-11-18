"""
복부 CT 장기 검출 판독문 프롬프트·후처리.

뇌 CT 와 같은 【검사】~【면책】 형식을 쓰되, 내용은 장기 위치 학습용이다.
질환 진단이 아니라 절편에서 보이는 장기 후보를 정리한다.
"""
from __future__ import annotations

import re

from .constants import CLASSES, class_ko
from .localization import summarize_targets

HIGH_CONFIDENCE_THRESHOLD = 0.50
REPORT_TOP_K = 5
_DISCLAIMER = "본 해석은 보조 의견이며 진단은 의사가 확인해야 합니다."

_ORGAN_NOTE = {
    "adrenal_gland": "부신은 신장 상극 근처의 작은 연부조직으로, 절편 높이에 따라 안 보일 수 있습니다.",
    "bladder": "방광은 골반 하부 중앙의 액체 음영으로 보일 수 있습니다.",
    "colon": "대장은 가스·분변 음영이 섞인 관상 구조로 복강 주변부에 보일 수 있습니다.",
    "duodenum": "십이지장은 췌장 머리 주변의 장관으로, 단일 절편만으로는 구분이 어려울 수 있습니다.",
    "esophagus": "식도는 종격 하부~복강 입구에서 원형 연부조직으로 보일 수 있습니다.",
    "gallbladder": "담낭은 간 아래 우상복부의 액체 음영으로 보일 수 있습니다.",
    "kidney": "신장은 후복막 좌우의 콩팥 모양 연부조직입니다. 좌우를 overlay 위치와 맞춰 봅니다.",
    "liver": "간은 우상복부~중앙의 큰 실질 장기로, 절편에서 가장 넓게 잡히는 경우가 많습니다.",
    "pancreatic": "췌장은 상복부 중앙의 가로로 긴 연부조직입니다. 지방이 적으면 경계가 흐릴 수 있습니다.",
    "prostate": "전립선은 방광 아래 골반 중앙의 연부조직입니다(남성).",
    "spleen": "비장은 좌상복부의 실질 장기로, 갈비뼈 아래쪽에서 보일 수 있습니다.",
    "stomach": "위는 좌상복부 가스·액체 음영이 섞인 공동으로 보일 수 있습니다.",
    "uterus": "자궁은 골반 중앙의 연부조직입니다(여성).",
}

_SECTION_ORDER = ("검사", "화질", "범위", "소견", "인상", "확실도", "면책")
_VALID_SECTION = re.compile(r"^[\[【](검사|화질|범위|소견|인상|확실도|면책|AI참고)[\]】]\s*(.*)$")


def top_report_findings(
    probs,
    *,
    min_score: float = 0.0,
    limit: int = REPORT_TOP_K,
) -> list[tuple[str, float]]:
    """CLASSES 순서 점수에서 검출된 장기만 점수 높은 순으로."""
    items = [
        (CLASSES[i], float(probs[i]))
        for i in range(min(len(probs), len(CLASSES)))
        if float(probs[i]) >= min_score and float(probs[i]) > 0
    ]
    items.sort(key=lambda x: -x[1])
    return items[:limit]


def _score_band(score: float) -> str:
    if score >= 0.70:
        return "높음"
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return "중등도"
    if score >= 0.30:
        return "낮음~중등도"
    return "낮음"


def _findings_scores(findings: list[tuple[str, float]]) -> str:
    return ", ".join(
        f"{class_ko(name)} {score:.0%}({_score_band(score)})" for name, score in findings
    )


def _location_phrase(per_class: dict, classes: list[str]) -> str:
    loc = summarize_targets(per_class, classes=classes)
    if not loc or loc in ("해당 없음", "강조된 장기 없음"):
        return "해당 절편"
    return loc


def build_report_extra(
    probs,
    per_class: dict,
    *,
    clinical: str | None = None,
) -> str:
    """MedGemma 프롬프트에 붙일 AI 힌트."""
    parts: list[str] = []
    if clinical:
        parts.append(f"임상: {clinical.strip()}")
    high = top_report_findings(probs)
    if high:
        parts.append(f"AI 장기 검출 상위: {_findings_scores(high)}")
    loc = summarize_targets(per_class, classes=list(CLASSES))
    if loc and loc != "강조된 장기 없음":
        parts.append(f"overlay 위치: {loc}")
    return " / ".join(parts) if parts else ""


def format_ai_reference_section(probs) -> str:
    high = top_report_findings(probs)
    if not high:
        return "【AI참고】 검출된 장기 없음."
    rest = [
        (CLASSES[i], float(probs[i]))
        for i in range(min(len(probs), len(CLASSES)))
        if CLASSES[i] not in {name for name, _ in high} and float(probs[i]) > 0
    ]
    rest.sort(key=lambda x: -x[1])
    rest_txt = ", ".join(f"{class_ko(n)} {p:.0%}" for n, p in rest[:4]) if rest else "없음"
    return (
        f"【AI참고】 장기 검출 상위: {_findings_scores(high)}. 그 외: {rest_txt}."
    )


def merge_ai_reference(report: str, probs) -> str:
    disclaimer = _DISCLAIMER
    if "【면책】" in report:
        head, _, tail = report.partition("【면책】")
        rest = tail.strip()
        first = rest.split("\n", 1)[0].strip() if rest else ""
        if len(first) > 5:
            disclaimer = first
    else:
        head = report
    for marker in ("【AI참고】", "【확률】"):
        if marker in head:
            head = head.split(marker)[0].rstrip()
    return (
        head.rstrip()
        + "\n"
        + format_ai_reference_section(probs)
        + "\n"
        + f"【면책】 {disclaimer}"
    )


def make_radiology_prompt(extra_context: str | None = None, *, dual_image: bool = False) -> str:
    hint = extra_context.strip() if extra_context else ""
    hint_line = f"\n참고(영상과 다르면 무시): {hint}" if hint else ""
    image_line = (
        "첨부: (1) 복부 CT ROI, (2) AI overlay(장기 박스, 정답 아님)."
        if dual_image
        else "첨부: 복부 CT ROI 1장."
    )
    return f"""You are a helpful radiology assistant. {image_line}
Write a detailed Korean learning note for this abdomen CT ROI organ localization.{hint_line}

Rules:
- Output only the final report in Korean (no English, no reasoning, no tags).
- This is organ localization for learning, NOT disease diagnosis.
- 【소견】: list detected organs with approximate location; do not invent pathology.
- 【인상】: summarize which organs are likely visible on this single axial slice.
- Do not give treatment advice.

Fill exactly this format and stop after 【면책】:

【검사】 전산화단층촬영 / 복부 / 축상면 / 비조영
【화질】 ...
【범위】 ...
【소견】 ...
【인상】 ...
【확실도】 ...
【면책】 {_DISCLAIMER}
"""


def build_template_report(probs, per_class: dict) -> str:
    """API·다운로드 없이 앙상블(검출) 점수만으로 판독문 초안을 만든다."""
    findings = top_report_findings(probs)
    loc = _location_phrase(per_class, list(CLASSES))
    if findings:
        finding_lines = []
        for name, score in findings:
            note = _ORGAN_NOTE.get(name, "")
            finding_lines.append(
                f"- {class_ko(name)} (검출 {score:.0%}, {_score_band(score)}). {note}"
            )
        if loc != "해당 절편":
            finding_lines.insert(0, f"- overlay 위치: {loc}.")
        findings_body = "\n".join(finding_lines)
        impression_lines = [
            f"{i + 1}. {class_ko(name)} ({score:.0%}, {_score_band(score)}) — "
            f"이 절편에서 해당 장기로 표시되었습니다."
            for i, (name, score) in enumerate(findings[:3])
        ]
        impression_lines.append(
            f"{len(impression_lines) + 1}. 단일 축상 절편·AI 박스 기준이므로 "
            "연속 절편과 임상 정보로 확인이 필요합니다."
        )
        impression = "\n".join(impression_lines)
        top = findings[0][1]
        if top >= 0.70:
            certainty = "중간~높음 — 상위 검출은 뚜렷하나 단일 절편·학습용 참고입니다."
        elif top >= HIGH_CONFIDENCE_THRESHOLD:
            certainty = "중간 — 검출 점수가 있으나 위치·경계는 연속 절편에서 재확인하세요."
        else:
            certainty = "낮음~중간 — 검출 점수가 낮아 참고 수준의 장기 후보입니다."
        quality = "화면 공유 ROI 기준. 창 설정·움직임·잘림을 함께 봅니다."
        scope = "복부 축상면 단일 절편(장기 위치 학습용)."
    else:
        findings_body = (
            "- 설정 기준값 이상으로 표시된 장기가 없습니다. "
            "없는 것이 아니라 이 절편·이 기준에서는 박스가 안 나온 것입니다."
        )
        impression = (
            "1. 검출 없음 — 절편 높이·창 설정·기준값을 바꿔 다시 보거나 "
            "연속 절편을 확인하세요.\n"
            "2. 본 결과는 학습용 장기 위치 참고입니다."
        )
        certainty = "낮음 — 검출 없음, 단일 절편."
        quality = "화면 공유 ROI 기준."
        scope = "복부 축상면 단일 절편."

    return "\n".join(
        [
            "【검사】 전산화단층촬영 / 복부 / 축상면 / 비조영",
            f"【화질】 {quality}",
            f"【범위】 {scope}",
            f"【소견】\n{findings_body}",
            f"【인상】\n{impression}",
            f"【확실도】 {certainty}",
            f"【면책】 {_DISCLAIMER}",
        ]
    )


def normalize_korean_report(text: str, probs, per_class: dict) -> str:
    """LLM 출력이 깨져도 섹션을 맞춰 돌려준다. 실패하면 template 으로 대체."""
    text = re.sub(r"<think>.*?(?:</think>|$)", "", text, flags=re.S | re.I)
    text = text.replace("[", "【").replace("]", "】")
    if "【AI참고】" in text:
        text = text.split("【AI참고】")[0]
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = _VALID_SECTION.match(s)
        if m:
            if current:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1)
            if current == "AI참고":
                break
            buf = [m.group(2).strip()] if m.group(2).strip() else []
            continue
        if current:
            buf.append(s)
    if current and current != "AI참고":
        sections[current] = "\n".join(buf).strip()
    if "소견" not in sections or "인상" not in sections:
        return build_template_report(probs, per_class)
    ordered = []
    for name in _SECTION_ORDER:
        body = sections.get(name)
        if body:
            ordered.append(f"【{name}】 {body}" if "\n" not in body else f"【{name}】\n{body}")
        elif name == "면책":
            ordered.append(f"【면책】 {_DISCLAIMER}")
        elif name == "검사":
            ordered.append("【검사】 전산화단층촬영 / 복부 / 축상면 / 비조영")
    return "\n".join(ordered)


class ReportFormat:
    """판독문 프롬프트·정규화 진입점."""

    top_report_findings = staticmethod(top_report_findings)
    build_report_extra = staticmethod(build_report_extra)
    build_template_report = staticmethod(build_template_report)
    make_radiology_prompt = staticmethod(make_radiology_prompt)
    merge_ai_reference = staticmethod(merge_ai_reference)
    normalize_korean_report = staticmethod(normalize_korean_report)
