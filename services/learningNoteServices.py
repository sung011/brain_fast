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
        "appearance": ["영상에서 보이는 모습(판독 단서)", ...],
        "locations": ["하부-환자 우측", ...],  # 이 문제의 의심 부위 위치. 채점 결과로 채운다(RAG 는 비워도 됨)
        "related": ["원인이 될 수 있는 질환 — 짧은 이유", ...],
        "combinations": ["다른 소견과 같이 보이면 …", ...],
        "pitfall": "헷갈리기 쉬운 점",
        "references": [{"title": "...", "url": "https://..."}],
        "disclaimer": "학습용 참고 정보이며 진단이 아닙니다 …",
        "source": "static" | "rag",          # 어디서 왔는지
    }
목록 칸은 비어 있어도 되지만 키는 모두 있어야 한다(화면이 그대로 그린다).
"""
from __future__ import annotations

from importlib import import_module

THORAX_PART = "2"
NOTE_KEYS = ("kind", "code", "name", "name_en", "summary", "appearance", "locations", "related",
             "combinations", "pitfall", "references", "disclaimer")
LIST_KEYS = ("appearance", "locations", "related", "combinations", "references")
TEXT_KEYS = ("code", "name", "name_en", "summary", "pitfall", "disclaimer")
# 부위별 정답의 성격. 흉부 문제의 정답은 소견, 그 밖은 병명
KIND_BY_PART = {THORAX_PART: "finding"}


def get_learning_note(st_part: str | None, disease: str | None, targets: list[dict] | None = None) -> dict | None:
    """정답(소견·병명)으로 학습 해설을 고른다. RAG → 고정 해설 순서. 둘 다 없으면 None.
    targets 는 채점 응답의 의심 부위(0~1 비율 박스)로, "이 문제에서는" 위치를 채우는 데 쓴다."""
    if not disease or not str(disease).strip():
        return None
    for source, provider in (("rag", _from_rag), ("static", _from_static)):
        note = provider(st_part, disease, targets)
        if note:
            return _normalize(note, source, st_part)
    return None


def _from_rag(st_part: str | None, disease: str, targets: list[dict] | None) -> dict | None:
    """의학용어 사전(RAG) 자리. 연결 전이라 늘 None → 고정 해설을 쓴다."""
    return None


def _from_static(st_part: str | None, disease: str, targets: list[dict] | None) -> dict | None:
    """부위별 고정 해설. 지금은 흉부만 있다."""
    if st_part != THORAX_PART:
        return None
    return import_module("class.thorax.report_format").learning_note(disease, targets)


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
