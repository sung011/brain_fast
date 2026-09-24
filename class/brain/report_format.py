"""
MedGemma 판독문 프롬프트·후처리 모듈.

- make_radiology_prompt: LLM에 보낼 한국어 판독 지시문
- normalize_korean_report: LLM 출력 → 【검사】~【확실도】 섹션 정규화
- merge_ai_reference: 【AI참고】 확률 섹션 + 【면책】 병합
"""
from __future__ import annotations

import re

import numpy as np

from .constants import CLASSES, class_ko
from .localization import summarize_targets

# 확신 표현용(필터가 아님). 판독에는 점수와 무관하게 상위 2개를 넣는다.
HIGH_CONFIDENCE_THRESHOLD = 0.60
REPORT_TOP_K = 2
_DISCLAIMER = "본 해석은 보조 의견이며 진단은 의사가 확인해야 합니다."

_FINDING_MORPH = {
    "epidural": (
        "렌즈형(양쪽이 볼록한) 고밀도 음영이 두개골 내판과 경막 사이에 국한되고, "
        "봉합선을 잘 넘지 않는 양상이면 경막외출혈에 부합합니다."
    ),
    "intraparenchymal": (
        "뇌실질 안에 국한된 고밀도 혈종과 주변 저밀도 부종이 있으면 뇌실질내출혈을 시사하며, "
        "뇌실 파급·종괴효과 여부를 함께 봅니다."
    ),
    "intraventricular": (
        "측뇌실·제3·제4뇌실 안에 고밀도 혈액이 보이면 뇌실내출혈입니다. "
        "뇌실 확장(수두증)과 뇌실주위 삼출 여부를 확인해야 합니다."
    ),
    "subarachnoid": (
        "실비우스열, 대뇌반구 열구, 기저조 등 뇌척수액 공간에 고밀도가 차면 "
        "지주막하출혈에 합당합니다."
    ),
    "subdural": (
        "초승달형 고밀도 음영이 대뇌반구를 따라 넓게 퍼지고 봉합선을 넘으면 "
        "경막하출혈을 우선 고려합니다."
    ),
    "any": (
        "두개내출혈은 유형을 가리지 않은 종합 점수입니다. "
        "화면에 보이는 고밀도 음영이 출혈일 가능성을 나타내며, 구체 유형은 함께 제시된 2순위 소견과 맞춰 봅니다."
    ),
    "tumor": (
        "송과체·안장상부 등에서 경계가 비교적 분명한 종괴 음영과 주변 구조 압박이 있으면 "
        "생식세포종양(germinoma) 가능성을 고려합니다. 단일 축상면만으로는 확정할 수 없습니다."
    ),
}
_SECTION_ORDER = ("검사", "화질", "범위", "소견", "인상", "확실도", "면책")
_VALID_SECTION = re.compile(r"^[\[【](검사|화질|범위|소견|인상|확실도|면책|AI참고)[\]】]\s*(.*)$")
_EMPTY_SECTION = re.compile(r"^【\s*】\s*(.*)$")


def top_report_findings(
    probs,
    *,
    min_score: float = 0.0,
    limit: int = REPORT_TOP_K,
) -> list[tuple[str, float]]:
    """화면에 보이는 분류 점수 그대로 상위 2개. 두개내출혈(any)도 포함."""
    items = [
        (CLASSES[i], float(probs[i]))
        for i in range(len(probs))
        if float(probs[i]) >= min_score
    ]
    items.sort(key=lambda x: -x[1])
    return items[:limit]


def _score_band(score: float) -> str:
    if score >= 0.80:
        return "높음"
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return "중등도"
    if score >= 0.40:
        return "낮음~중등도"
    return "낮음"


def _findings_labels(findings: list[tuple[str, float]]) -> str:
    return ", ".join(class_ko(name) for name, _ in findings)


def _findings_scores(findings: list[tuple[str, float]]) -> str:
    return ", ".join(
        f"{class_ko(name)} {score:.0%}({_score_band(score)})" for name, score in findings
    )


def _rank_word(index: int) -> str:
    return "1순위" if index == 0 else "2순위"


def _build_detailed_findings(findings: list[tuple[str, float]], loc: str) -> str:
    lines: list[str] = []
    if loc and loc != "해당 절편":
        lines.append(f"- overlay 의심 위치: {loc}.")
    for i, (name, score) in enumerate(findings):
        label = class_ko(name)
        morph = _FINDING_MORPH.get(name, "")
        prefix = "최우선" if i == 0 else "차순위 감별"
        lines.append(
            f"- {_rank_word(i)} {prefix}: {label}(AI 분류 {score:.0%}, 확신 {_score_band(score)}). {morph}"
        )
    if findings:
        top_score = findings[0][1]
        if top_score < HIGH_CONFIDENCE_THRESHOLD:
            lines.append(
                "- 최상위 분류가 60% 미만이므로 확정이 아니라 감별 목록입니다. "
                "단일 축상 절편이라 혈종 범위·두께·종괴효과·수두증은 연속 영상에서 재평가해야 합니다."
            )
        else:
            lines.append(
                "- 단일 축상 절편 기준 판독이므로 혈종 범위·두께·정중선 편위·수두증은 "
                "연속 절편 및 임상 소견과 함께 확인하는 것이 필요합니다."
            )
    return "\n".join(lines)


def _build_detailed_impression(findings: list[tuple[str, float]]) -> str:
    lines: list[str] = []
    for i, (name, score) in enumerate(findings):
        label = class_ko(name)
        band = _score_band(score)
        if i == 0:
            if name == "any":
                note = (
                    "유형을 가리지 않은 종합 점수입니다. 출혈 존재 가능성을 나타내며, "
                    "구체 위치·형태는 2순위 유형과 영상을 함께 봐야 합니다."
                )
            elif score >= HIGH_CONFIDENCE_THRESHOLD:
                note = (
                    f"{label}을 우선 시사합니다. 형태가 전형적이면 급성 출혈에 합당하나 "
                    "단일 ROI이므로 위치·양을 확정하기는 이릅니다."
                )
            else:
                note = (
                    f"{label}은 앙상블 최상위이나 확신은 {band}입니다. "
                    "위양성 가능성과 다른 출혈 유형을 함께 열어 두어야 합니다."
                )
            lines.append(f"1. {label} ({score:.0%}, {band}) — {note}")
        elif name == "any":
            lines.append(
                f"2. {label} ({score:.0%}, {band}) — 종합 출혈 점수입니다. "
                "1순위 구체 유형과 맞춰 해석하고, 연속 절편에서 재확인이 필요합니다."
            )
        else:
            lines.append(
                f"2. {label} ({score:.0%}, {band}) — 차순위 감별입니다. "
                "열구·뇌조·뇌실질·경막하 공간을 추가 절편에서 비교해 배제 또는 확인이 필요합니다."
            )
    if not lines:
        return "1. 임상 상관 및 추가 영상 검토 필요."
    lines.append(
        f"{len(lines) + 1}. 본 판독은 AI 보조 해석입니다. 신경학적 상태, 외상력, "
        "전체 CT 시리즈와 대조 후 최종 판단을 권고합니다."
    )
    return "\n".join(lines)


def _certainty_from_top(findings: list[tuple[str, float]]) -> str:
    if not findings:
        return "낮음 — 뚜렷한 우세 유형 없음, 단일 축상 절편."
    top = findings[0][1]
    if top >= 0.80:
        return "중간~높음 — 최상위 분류는 뚜렷하나 단일 ROI·단일 절편 제한이 있습니다."
    if top >= HIGH_CONFIDENCE_THRESHOLD:
        return "중간 — 60% 이상 우세 유형이 있으나 단일 축상 절편·AI 보조 분석입니다."
    return "낮음~중간 — 최상위 분류가 60% 미만이므로 참고 수준의 감별입니다."


def _location_phrase(per_class: dict, targets: list[int]) -> str:
    loc = summarize_targets(per_class, targets)
    if not loc or loc in ("해당 없음", "강조된 이상 부위 없음"):
        return "해당 절편"
    return loc


def build_report_extra(
    probs,
    per_class: dict,
    targets: list[int],
    clinical: str | None = None,
    germinoma_probs=None,
) -> str:
    """MedGemma 프롬프트에 붙일 짧은 AI 힌트 (영상 우선)."""
    parts: list[str] = []
    if clinical:
        parts.append(f"임상: {clinical.strip()}")
    high = top_report_findings(probs)
    if high:
        parts.append(
            f"AI 출혈 분류 상위 {len(high)}개(점수 무관, 소견·인상에 형태까지 자세히 기술): {_findings_scores(high)}"
        )
    if germinoma_probs is not None and len(germinoma_probs) > 0:
        g_score = float(germinoma_probs[0])
        parts.append(
            f"AI germinoma(tumor) 점수: {class_ko('tumor')} {g_score:.0%}({_score_band(g_score)})"
        )
    loc = summarize_targets(per_class, targets)
    if loc and loc != "해당 없음":
        parts.append(f"overlay 의심 부위: {loc}")
    return " / ".join(parts) if parts else ""


def format_ai_reference_section(probs, germinoma_probs=None) -> str:
    high = top_report_findings(probs)
    if not high:
        ich_txt = "분류 결과 없음"
    else:
        rest = [
            (CLASSES[i], float(probs[i]))
            for i in range(len(probs))
            if CLASSES[i] not in {name for name, _ in high} and CLASSES[i] != "any"
        ]
        rest.sort(key=lambda x: -x[1])
        rest_txt = ", ".join(f"{class_ko(n)} {p:.0%}" for n, p in rest[:3]) if rest else "없음"
        ich_txt = (
            f"출혈 앙상블 상위 {len(high)}개: {_findings_scores(high)}. "
            f"그 외: {rest_txt}."
        )
    bits = [ich_txt]
    if germinoma_probs is not None and len(germinoma_probs) > 0:
        g_score = float(germinoma_probs[0])
        bits.append(f"germinoma: {class_ko('tumor')} {g_score:.0%}({_score_band(g_score)}).")
    return "【AI참고】 " + " ".join(bits)


def merge_ai_reference(report: str, probs, germinoma_probs=None) -> str:
    """판독문 끝에 【AI참고】 앙상블 확률 + 【면책】 섹션을 붙인다."""
    disclaimer = _DISCLAIMER
    if "【면책】" in report:
        head, _, tail = report.partition("【면책】")
        rest = tail.strip()
        if rest.startswith("【면책】"):
            rest = rest[len("【면책】") :].strip()
        first = rest.split("\n", 1)[0].strip() if rest else ""
        if len(first) > 5:
            disclaimer = first
    else:
        head = report
    for marker in ("【AI참고】", "【확률】", "【의심】"):
        if marker in head:
            head = head.split(marker)[0].rstrip()
    return (
        head.rstrip()
        + "\n"
        + format_ai_reference_section(probs, germinoma_probs)
        + "\n"
        + f"【면책】 {disclaimer}"
    )


def make_radiology_prompt(extra_context: str | None = None, *, dual_image: bool = False) -> str:
    """MedGemma(HF) 단일턴·간결 판독 프롬프트."""
    hint = extra_context.strip() if extra_context else ""
    hint_line = f"\n참고(영상과 다르면 무시): {hint}" if hint else ""
    image_line = (
        "첨부: (1) 뇌 CT ROI, (2) AI overlay(의심 부위 표시, 정답 아님)."
        if dual_image
        else "첨부: 뇌 CT ROI 1장."
    )
    return f"""You are a helpful radiology assistant. {image_line}
Write a detailed Korean radiology report for this brain CT ROI.{hint_line}

Rules:
- Output only the final report in Korean (no English, no reasoning, no tags).
- 【소견】: 2-3 bullet lines. Describe density, likely shape (crescent/lens/sulcal/parenchymal), and both AI top-2 types from the hint even if scores are below 60%.
- 【인상】: numbered list covering both top-2 types as primary and differential, plus a caution that this is a single axial slice.
- Do not write "이상 없음" if the hint lists two hemorrhage types.
- Do not give treatment advice or repeat the same phrase.

Fill exactly this format and stop after 【면책】:

【검사】 전산화단층촬영 / 두부 / 축상면 / 비조영
【화질】 ...
【범위】 ...
【소견】 ...
【인상】 ...
【확실도】 ...
【면책】 {_DISCLAIMER}
"""


def _trim_range(body: str, max_items: int = 6) -> str:
    parts = [re.sub(r"\s+", " ", p).strip(" .") for p in re.split(r"[,，、]", body) if p.strip()]
    seen, out = set(), []
    for p in parts:
        key = re.sub(r"^뇌", "", p)
        if not p or p in seen or key in seen:
            continue
        seen.add(p)
        seen.add(key)
        out.append(p)
        if len(out) >= max_items:
            break
    return ", ".join(out)


def _guess_section(body: str) -> str | None:
    b = body.strip()
    if not b:
        return None
    if re.search(r"전산화|단층\s*촬영|CT|MRI|엑스선|조영|축상|관상|시상", b, re.I) and ("/" in b or "비조영" in b):
        return "검사"
    if re.search(r"화질|선명|노출|움직임|아티팩|인공", b):
        return "화질"
    if re.search(r"^(두개골|뇌실|피질|소뇌|뇌간)", b) and "고밀도" not in b and "출혈" not in b and len(b) < 60:
        return "범위"
    if re.search(r"급성\s*이상|고밀도|출혈|저밀도|음영|종괴|부종|편위|눈에\s*보이", b):
        return "소견"
    if re.search(r"^(높음|중간|낮음)|확실|가능성이\s*(?:있|높)", b):
        return "확실도"
    if re.search(r"^1[\.\)]|시사|의심|배제|관찰\s*필요", b):
        return "인상"
    if re.search(r"참고용|면책|의료진|전문\s*의|진단은", b):
        return "면책"
    return None


def _clean_raw_report(text: str) -> str:
    text = re.sub(r"<think>.*?(?:</think>|$)", "", text, flags=re.S | re.I)
    text = re.sub(r"<unused94>.*?(?:<unused95>|$)", "", text, flags=re.S)
    text = re.sub(r"<unused\d+>", "", text)
    text = text.replace("[", "【").replace("]", "】")
    if "【AI참고】" in text:
        text = text.split("【AI참고】")[0]
    for marker in ("【검사】", "【 】"):
        if marker in text:
            text = text[text.index(marker) :]
            break
    if "【면책】" in text:
        end = text.index("【면책】")
        rest = text[end:]
        nl = rest.find("\n")
        text = text[: end + (len(rest) if nl < 0 else nl + 1)]
    return text.strip()


def _parse_sections_flexible(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    orphan: list[str] = []
    current: str | None = None
    buf: list[str] = []

    def flush():
        nonlocal current, buf
        if current:
            sections[current] = "\n".join(buf).strip()
        elif buf:
            orphan.append("\n".join(buf).strip())
        current = None
        buf.clear()

    for line in text.splitlines():
        s = line.strip()
        if not s or s in ("【 】, 【 】", "【】,【】"):
            continue
        m = _VALID_SECTION.match(s)
        if m:
            flush()
            current = m.group(1)
            if m.group(1) == "AI참고":
                break
            if m.group(2).strip():
                buf = [m.group(2).strip()]
            continue
        m2 = _EMPTY_SECTION.match(s)
        if m2:
            flush()
            body = m2.group(1).strip().strip(",").strip()
            if body:
                orphan.append(body)
            continue
        if current:
            buf.append(s)
        elif orphan:
            orphan[-1] = orphan[-1] + "\n" + s
        else:
            orphan.append(s)
    flush()

    order_i = 0
    for content in orphan:
        if not content.strip():
            continue
        name = _guess_section(content)
        if not name:
            while order_i < len(_SECTION_ORDER) and _SECTION_ORDER[order_i] in sections:
                order_i += 1
            name = _SECTION_ORDER[order_i] if order_i < len(_SECTION_ORDER) else "소견"
            order_i += 1
        if name in sections:
            if name == "소견":
                sections[name] = sections[name] + "\n- " + content.lstrip("- ")
            elif name == "범위" and "고밀도" in content:
                sections["소견"] = sections.get("소견", "") + ("\n" if sections.get("소견") else "") + "- " + content
            else:
                sections.setdefault("소견", "- " + content)
        else:
            if name == "범위":
                sections[name] = _trim_range(content) or content
            elif name == "소견" and not content.startswith("-"):
                sections[name] = "- " + content
            elif name == "인상" and not re.match(r"^1[\.\)]", content):
                sections[name] = "1. " + content
            else:
                sections[name] = content
    return sections


def _mentions_no_acute(body: str) -> bool:
    return bool(
        re.search(
            r"급성\s*이상.*없|이상\s*소견.*없|이상\s*없|병변.*없|명확하지\s*않",
            body,
        )
    )


def _mentions_all_findings(body: str, findings: list[tuple[str, float]]) -> bool:
    return all(class_ko(name) in body for name, _ in findings)


def _append_germinoma_to_findings(
    findings_body: str,
    impression: str,
    germinoma_probs,
) -> tuple[str, str]:
    if germinoma_probs is None or len(germinoma_probs) == 0:
        return findings_body, impression
    g_score = float(germinoma_probs[0])
    if g_score < 0.35:
        return findings_body, impression
    g_findings = [("tumor", g_score)]
    g_line = _build_detailed_findings(g_findings, "해당 절편")
    g_imp = _build_detailed_impression(g_findings)
    if class_ko("tumor") not in findings_body:
        findings_body = (findings_body + "\n" + g_line).strip() if findings_body else g_line
    if class_ko("tumor") not in impression:
        impression = (impression + "\n" + g_imp).strip() if impression else g_imp
    return findings_body, impression


def _fill_and_polish_sections(
    sections: dict,
    probs,
    per_class: dict,
    targets: list[int],
    germinoma_probs=None,
) -> dict:
    sections.setdefault("검사", "전산화단층촬영 / 두부 / 축상면 / 비조영")
    if "검사" in sections:
        sections["검사"] = re.sub(r"\s+", " ", sections["검사"]).replace("조영제 사용 안 함", "비조영")

    sections.setdefault("화질", "단일 절편, 판독 가능.")
    sections.setdefault("범위", "두개골, 뇌실질, 뇌실")
    if sections.get("범위"):
        sections["범위"] = _trim_range(sections["범위"], max_items=4) or sections["범위"]

    loc = _location_phrase(per_class, targets)
    high = top_report_findings(probs)
    findings_body = sections.get("소견", "").strip()
    impression = sections.get("인상", "").strip()

    if high:
        detailed = _build_detailed_findings(high, loc)
        if not findings_body or _mentions_no_acute(findings_body) or len(findings_body) < 40:
            sections["소견"] = detailed
        elif not _mentions_all_findings(findings_body, high):
            missing = [item for item in high if class_ko(item[0]) not in findings_body]
            extra = _build_detailed_findings(missing, loc) if missing else ""
            sections["소견"] = findings_body + (("\n" + extra) if extra else "")

        if not impression or _mentions_no_acute(impression) or len(impression) < 20:
            sections["인상"] = _build_detailed_impression(high)
        elif not _mentions_all_findings(impression, high):
            sections["인상"] = impression + "\n" + _build_detailed_impression(high)
    else:
        if not findings_body:
            sections["소견"] = "- 눈에 보이는 범위에서 급성 이상 소견은 없습니다."
        if not impression:
            sections["인상"] = "1. 임상 상관 및 추가 영상 검토 필요."

    findings_body, impression = _append_germinoma_to_findings(
        sections.get("소견", "").strip(),
        sections.get("인상", "").strip(),
        germinoma_probs,
    )
    sections["소견"] = findings_body
    sections["인상"] = impression

    if not sections.get("확실도", "").strip():
        sections["확실도"] = _certainty_from_top(high)

    sections["면책"] = _DISCLAIMER
    return sections


def format_report_from_sections(sections: dict) -> str:
    lines: list[str] = []
    for name in ("검사", "화질", "범위", "소견", "인상", "확실도"):
        body = sections.get(name, "").strip()
        if name in ("소견", "인상"):
            lines.append(f"【{name}】")
            lines.append(
                body
                or ("- 눈에 보이는 범위에서 급성 이상 소견은 없습니다." if name == "소견" else "1. 판단 어려움")
            )
        else:
            lines.append(f"【{name}】 {body or '판단 어려움'}")
    return "\n".join(lines)


def build_template_report(
    probs,
    per_class: dict,
    targets: list[int],
    germinoma_probs=None,
) -> str:
    """MedGemma API 없이 앙상블·overlay 결과만으로 상세 한국어 판독문 생성."""
    loc = _location_phrase(per_class, targets)
    high = top_report_findings(probs)
    if high:
        sections = {
            "검사": "전산화단층촬영 / 두부 / 축상면 / 비조영",
            "화질": "화면 캡처 ROI 단일 절편. 움직임 인공음영은 제한적으로만 평가되며 판독은 가능합니다.",
            "범위": "두개골, 뇌실질, 뇌실, 뇌조·열구(보이는 범위)",
            "소견": _build_detailed_findings(high, loc),
            "인상": _build_detailed_impression(high),
            "확실도": _certainty_from_top(high),
        }
    else:
        sections = {
            "검사": "전산화단층촬영 / 두부 / 축상면 / 비조영",
            "화질": "화면 캡처 ROI 단일 절편, 판독 가능.",
            "범위": "두개골, 뇌실질, 뇌실",
            "소견": "- 눈에 보이는 범위에서 급성 이상 소견은 없습니다.",
            "인상": "1. 급성 두개내 병변은 명확하지 않음.\n2. 전체 시리즈 재검토를 권고합니다.",
            "확실도": "낮음 — 우세 유형 없음, 단일 축상 절편.",
        }
    findings_body, impression = _append_germinoma_to_findings(
        sections["소견"], sections["인상"], germinoma_probs
    )
    sections["소견"] = findings_body
    sections["인상"] = impression
    return format_report_from_sections(sections)


def normalize_korean_report(
    text: str,
    probs,
    per_class: dict,
    targets: list[int],
    germinoma_probs=None,
) -> str:
    """
    LLM raw 출력 → 섹션 파싱 → 빈 섹션 보정 → 최종 【검사】~【확실도】 문자열.

    MedGemma가 형식을 어기거나 【 】 빈 섹션을 낼 때 복구한다.
    """
    text = _clean_raw_report(text)
    sections = _parse_sections_flexible(text)
    sections = _fill_and_polish_sections(
        sections, probs, per_class, targets, germinoma_probs=germinoma_probs
    )
    return format_report_from_sections(sections)


class ReportFormat:
    """MedGemma 판독문 프롬프트·후처리 클래스."""

    top_report_findings = staticmethod(top_report_findings)
    build_report_extra = staticmethod(build_report_extra)
    build_template_report = staticmethod(build_template_report)
    make_radiology_prompt = staticmethod(make_radiology_prompt)
    merge_ai_reference = staticmethod(merge_ai_reference)
    normalize_korean_report = staticmethod(normalize_korean_report)
