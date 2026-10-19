"""
채점 결과 화면의 학습 해설(소견 설명·관련 질환) 서비스.

해설을 어디서 가져오든 화면에 나가는 모양은 하나로 맞춘다. 지금은 흉부 고정 해설
(class/thorax/report_format.py)만 있다. 팀장님이 만드는 의학용어 사전(RAG)이 준비되면
_from_rag() 만 채우면 되고, 라우터·화면은 고치지 않는다.

돌려주는 모양 (RAG 도 이 모양으로 돌려주면 된다):
    {
        "kind": "finding" | "disease",       # 정답이 소견(흉부)인지 병명(뇌)인지. 화면 제목이 "소견 해설"/"병명 해설"로 바뀐다
        "code": "pleural_effusion",          # 소견 코드 (없으면 이름 그대로)
        "name": "흉막삼출", "name_en": "Pleural effusion",
        "summary": "이 소견(병명)은 — 한두 문장 정의",
        "appearance": ["판독 포인트 — 이 소견이면 보통 이렇게 보인다", ...],
        "locations": ["하부-환자 우측", ...],  # 이 영상의 의심 부위 위치. 채점 결과로 채운다(RAG 는 비워도 됨)
        "case_summary": "이 영상에서는 … 의심 부위가 1곳 있습니다: …",  # 채점 결과로 만든 문장(RAG 는 비워도 됨)
        "related": ["감별할 질환 — 짧은 이유", ...],
        "confirm": ["확인하려면 — 원인 질환을 확인하는 검사", ...],
        "confirm_note": "X-ray 는 소견까지만 … (확진할 수 없다는 안내)",
        "combinations": ["다른 소견과 같이 보이면 …", ...],
        "pitfall": "헷갈리기 쉬운 점",
        "references": [{"title": "...", "url": "https://..."}],
        "disclaimer": "학습용 참고 정보이며 진단이 아닙니다 …",
        "source": "static" | "rag",          # 어디서 왔는지
        "summary_source": "glossary" | ...,  # "이 소견은" 정의를 의학용어 사전(GLOSSARY_CSV_PATH)에서 가져왔으면 glossary
    }
목록 칸은 비어 있어도 되지만 키는 모두 있어야 한다(화면이 그대로 그린다).
"""
from __future__ import annotations

import csv
from functools import lru_cache
from importlib import import_module
from pathlib import Path

from config import settings

THORAX_PART = "2"
NOTE_KEYS = ("kind", "code", "name", "name_en", "summary", "appearance", "locations", "case_summary", "related",
             "confirm", "confirm_note", "combinations", "pitfall", "references", "disclaimer")
LIST_KEYS = ("appearance", "locations", "related", "confirm", "combinations", "references")
TEXT_KEYS = ("code", "name", "name_en", "summary", "case_summary", "confirm_note", "pitfall", "disclaimer")
# 부위별 정답의 성격. 흉부 문제의 정답은 소견, 그 밖은 병명
KIND_BY_PART = {THORAX_PART: "finding"}


def get_learning_note(st_part: str | None, disease: str | None, targets: list[dict] | None = None) -> dict | None:
    """정답(소견·병명)으로 학습 해설을 고른다. RAG → 고정 해설 순서. 둘 다 없으면 None.
    targets 는 채점 응답의 의심 부위(0~1 비율 박스)로, "이 영상에서는" 위치를 채우는 데 쓴다."""
    if not disease or not str(disease).strip():
        return None
    for source, provider in (("rag", _from_rag), ("static", _from_static)):
        note = provider(st_part, disease, targets)
        if note:
            return _with_glossary_definition(_normalize(note, source, st_part))
    return None


def _from_rag(st_part: str | None, disease: str, targets: list[dict] | None) -> dict | None:
    """의학용어 사전(RAG) 자리. 연결 전이라 늘 None → 고정 해설을 쓴다."""
    return None


def _from_static(st_part: str | None, disease: str, targets: list[dict] | None) -> dict | None:
    """부위별 고정 해설. 지금은 흉부만 있다."""
    if st_part != THORAX_PART:
        return None
    return import_module("class.thorax.report_format").learning_note(disease, targets)


def _with_glossary_definition(note: dict) -> dict:
    """의학용어 사전(팀 RAG 자료 CSV)에 같은 영문 용어가 있으면 "이 소견은" 정의를 사전 글로 바꾼다.
    검색(RAG)이 아니라 영문 이름이 정확히 같은 행만 쓴다. 사전이 없거나 못 찾으면 그대로 둔다."""
    definition = glossary_definition(note.get("name_en") or note.get("code") or "")
    if definition:
        note["summary"] = definition
        note["summary_source"] = "glossary"
    else:
        note.setdefault("summary_source", note["source"])
    return note


def glossary_definition(term_en: str) -> str:
    """영문 용어 → 사전의 한글 정의(첫 문장 두 개까지). 없으면 빈 문자열."""
    key = term_en.strip().lower().replace("_", " ")
    if not key:
        return ""
    text = _glossary(settings.glossary_csv_path).get(key, "")
    if not text:
        return ""
    sentences = [s.strip() for s in text.replace("\n", " ").split(". ") if s.strip()]
    short = ". ".join(sentences[:2])
    return short if short.endswith(".") else short + "."


@lru_cache(maxsize=2)
def _glossary(path: str) -> dict[str, str]:
    """사전 CSV 를 한 번만 읽어 {영문 소문자: 한글 정의} 로 둔다. 같은 용어가 여러 번이면 처음 것."""
    if not path or not Path(path).is_file():
        return {}
    out: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            en = (row.get("en") or "").strip().lower()
            kr = (row.get("definition_kr") or "").strip()
            if en and kr and en not in out:
                out[en] = kr
    return out


def _normalize(note: dict, source: str, st_part: str | None) -> dict:
    """빠진 키를 채워 화면이 늘 같은 모양을 받게 한다."""
    out = {key: note.get(key) for key in NOTE_KEYS}
    for key in LIST_KEYS:
        out[key] = list(out[key] or [])
    for key in TEXT_KEYS:
        out[key] = out[key] or ""
    out["kind"] = out["kind"] or KIND_BY_PART.get(st_part, "disease")
    out["source"] = source
    return out
