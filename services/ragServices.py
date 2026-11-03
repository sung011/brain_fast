"""질문으로 의학용어 색인을 검색하고, GET /glossary 와 같은 항목 모양으로 바꾼다."""

from importlib import import_module


class RagUnavailable(Exception):
    """색인을 열지 못했다. 질문 형식 오류가 아니다."""


# 용어 사전 화면(localhost:8080/glossary)이 이미 받아 두는 뇌·흉부·복부·무릎 합계(3445)에 맞춘 상한.
# 화면은 그중 48개씩 보여 주고, 검색은 받아 둔 목록에서 한다.
GLOSSARY_RESULT_LIMIT = 3500


def search_glossary(question: str | None = None) -> dict:
    """
    질문에서 검색어를 뽑고 색인을 조회한다.

    질문이 비어 있으면 색인에서 용어와 병을 무작위로 3500건 고른다.
    질문이 있으면 맞는 항목을 개수 제한 없이 모두 돌려준다.
    용어를 먼저, 관련 병을 그 다음에 items 에 넣는다.
    색인에 없는 칸(id, 부위, 태그)은 null 이다.
    """
    text = (question or "").strip()
    if len(text) > 300:
        raise ValueError("검색어는 300자 이하여야 합니다.")

    rag = import_module("class.rag")
    try:
        if not text:
            found = rag.list_entries(limit=GLOSSARY_RESULT_LIMIT)
        else:
            if not rag.query_terms(text):
                raise ValueError("검색어가 없습니다. 증상이나 병명을 입력하세요.")
            found = rag.search(text)
    except rag.RagIndexError as exc:
        raise RagUnavailable(str(exc)) from exc

    items = [_item(row, "term") for row in found["glossary"]]
    items.extend(_item(row, "disease") for row in found["diseases"])
    return {
        "q": text,
        "terms": found["terms"],
        "count": len(items),
        "items": items,
    }


def _item(row: dict, category: str) -> dict:
    return {
        "id": None,
        "term_id": None,
        "name_ko": row["name"],
        "name_en": row.get("name_en"),
        "definition_ko": row["explanation"],
        "definition_en": row.get("definition_en"),
        "body_part": None,
        "tags": None,
        "tags_ko": None,
        "modality": None,
        "category": category,
    }
