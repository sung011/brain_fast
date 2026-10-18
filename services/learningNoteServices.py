"""
채점 결과 화면의 학습 해설(소견 설명·관련 질환) 서비스.

해설을 어디서 가져오든 화면에 나가는 모양은 하나로 맞춘다. 지금은 흉부 고정 해설
(class/thorax/report_format.py)만 있다. 팀장님이 만드는 의학용어 사전(RAG)이 준비되면
_from_rag() 만 채우면 되고, 라우터·화면은 고치지 않는다.

돌려주는 모양 (RAG 도 이 모양으로 돌려주면 된다):
    {
        "code": "pleural_effusion",          # 소견 코드 (없으면 병명 그대로)
        "name": "흉막삼출", "name_en": "Pleural effusion",
        "summary": "한두 문장 설명",
        "related": ["관련 질환 — 짧은 이유", ...],
        "combinations": ["다른 소견과 같이 보이면 …", ...],
        "check": ["같이 볼 점", ...],
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
NOTE_KEYS = ("code", "name", "name_en", "summary", "related", "combinations",
             "check", "pitfall", "references", "disclaimer")


def get_learning_note(st_part: str | None, disease: str | None) -> dict | None:
    """정답 병명으로 학습 해설을 고른다. RAG → 고정 해설 순서. 둘 다 없으면 None."""
    if not disease or not str(disease).strip():
        return None
    for source, provider in (("rag", _from_rag), ("static", _from_static)):
        note = provider(st_part, disease)
        if note:
            return _normalize(note, source)
    return None


def _from_rag(st_part: str | None, disease: str) -> dict | None:
    """의학용어 사전(RAG) 자리. 연결 전이라 늘 None → 고정 해설을 쓴다."""
    return None


def _from_static(st_part: str | None, disease: str) -> dict | None:
    """부위별 고정 해설. 지금은 흉부만 있다."""
    if st_part != THORAX_PART:
        return None
    return import_module("class.thorax.report_format").learning_note(disease)


def _normalize(note: dict, source: str) -> dict:
    """빠진 키를 채워 화면이 늘 같은 모양을 받게 한다."""
    out = {key: note.get(key) for key in NOTE_KEYS}
    for key in ("related", "combinations", "check", "references"):
        out[key] = list(out[key] or [])
    for key in ("code", "name", "name_en", "summary", "pitfall", "disclaimer"):
        out[key] = out[key] or ""
    out["source"] = source
    return out
