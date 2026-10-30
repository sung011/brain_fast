"""
질문으로 용어와 관련 병을 고른다.

단어가 이름·설명에 들어 있는 행을 먼저 고르고, 벡터 검색은 한글이 거의 없는 조각을 버린 뒤
그 행의 용어 이름으로 보여 준다. 개수 제한은 없다.
"""
import random

from .entries import (
    entry_record,
    hangul_count,
    is_disease,
    mention_text,
    name_aliases,
    short_english,
    short_explanation,
)
from .query import query_terms, variants
from . import store


def term_hits(terms: list[str], name: str, body: str):
    matched = []
    in_name = 0
    for term in terms:
        words = variants(term)
        if any(word in name or word in body for word in words):
            matched.append(term)
        if any(word in name for word in words):
            in_name += 1
    aliases = name_aliases(name)
    expanded = [word for term in terms for word in variants(term)]
    exact = any(alias in expanded for alias in aliases)
    starts = any(alias.startswith(word) for alias in aliases for word in expanded)
    return matched, in_name, exact, starts


def find_answers(question: str, chunks: dict | None = None):
    """
    질문과 맞는 용어, 관련 병을 고른다.

    chunks 를 넘기면 그 행 목록만 본다. 없으면 로컬 색인을 연다.
    반환은 (검색어, 용어 항목, 병 항목, 병 개수).
    항목은 (맞은 검색어 수, 이름 일치, 이름으로 시작, 이름 안 검색어 수, 이름, 설명, 영어 이름, 영어 설명).
    """
    terms = query_terms(question)
    rows = store.row_chunks() if chunks is None else chunks
    glossary = []
    diseases = []
    seen = set()
    for row_i in rows:
        record = entry_record(list(rows[row_i]))
        if not record:
            continue
        name, explanation = record["name"], record["explanation"]
        key = frozenset(name_aliases(name))
        if key in seen:
            continue
        body = mention_text(explanation)
        matched, in_name, exact, starts = term_hits(terms, name, body)
        if terms and not matched:
            continue
        item = (
            len(matched),
            int(exact),
            int(starts),
            in_name,
            name,
            explanation,
            record["name_en"],
            record["definition_en"],
        )
        if exact and not is_disease(name):
            seen.add(key)
            glossary.append(item)
            continue
        if not is_disease(name):
            continue
        seen.add(key)
        diseases.append(item)
    glossary.sort(key=lambda item: item[:4], reverse=True)
    diseases.sort(key=lambda item: item[:4], reverse=True)
    return terms, glossary, diseases, len(diseases)


def _pack(items) -> list[dict]:
    packed = []
    for matched, exact, starts, in_name, name, explanation, name_en, definition_en in items:
        packed.append(
            {
                "name": name,
                "name_en": name_en,
                "explanation": short_explanation(explanation),
                "definition_en": short_english(definition_en) if definition_en else None,
                "matched_count": matched,
                "exact": bool(exact),
                "starts": bool(starts),
                "in_name": in_name,
            }
        )
    return packed


def list_entries(chunks: dict | None = None, *, limit: int | None = None) -> dict:
    """
    색인에 있는 용어와 병을 돌려준다. 같은 이름은 한 번만 둔다.

    limit 가 있으면 그 개수만 무작위로 고른다. 전체보다 적으면 있는 만큼만 돌려준다.
    """
    rows = store.row_chunks() if chunks is None else chunks
    glossary = []
    diseases = []
    seen = set()
    for row_i in rows:
        record = entry_record(list(rows[row_i]))
        if not record:
            continue
        name = record["name"]
        key = frozenset(name_aliases(name))
        if key in seen:
            continue
        seen.add(key)
        item = (
            0,
            0,
            0,
            0,
            name,
            record["explanation"],
            record["name_en"],
            record["definition_en"],
        )
        if is_disease(name):
            diseases.append(item)
        else:
            glossary.append(item)
    if limit is not None and limit >= 0 and len(glossary) + len(diseases) > limit:
        picked = random.sample(glossary + diseases, limit)
        glossary = [item for item in picked if not is_disease(item[4])]
        diseases = [item for item in picked if is_disease(item[4])]
    glossary.sort(key=lambda item: item[4])
    diseases.sort(key=lambda item: item[4])
    return {
        "terms": [],
        "glossary": _pack(glossary),
        "diseases": _pack(diseases),
        "disease_count": len(diseases),
    }


def search(question: str, chunks: dict | None = None) -> dict:
    """API 가 그대로 실을 수 있는 검색 결과. 설명은 짧은 글이다. 진단이 아니다."""
    terms, glossary, diseases, total = find_answers(question, chunks=chunks)
    return {
        "terms": terms,
        "glossary": _pack(glossary),
        "diseases": _pack(diseases),
        "disease_count": total,
    }


def plain_answer(question: str, chunks: dict | None = None) -> str:
    """노트북에 인쇄하던 문장. 검색어, 용어, 관련 병 순서다."""
    terms, glossary, diseases, total = find_answers(question, chunks=chunks)
    lines = ["검색어: " + ", ".join(terms) if terms else "검색어: (없음)"]
    if glossary:
        lines.append("용어:")
        for item in glossary:
            lines.append("  " + item[4])
            lines.append("  " + short_explanation(item[5]))
    if not diseases:
        lines.append("관련 병: 자료에 없음")
        return "\n".join(lines)
    lines.append(f"관련 병 {total}:")
    for item in diseases:
        lines.append("병명: " + item[4])
        lines.append("설명: " + short_explanation(item[5]))
        lines.append("")
    return "\n".join(lines).rstrip()


def vector_hits(question: str, k: int | None = None, fetch_k: int = 80):
    """
    벡터 검색 뒤, 한글이 거의 없는 조각은 버리고 질문 단어가 있는 행만 남긴다.

    반환은 (점수, 이름, 설명). k 가 None 이면 개수 제한이 없다.
    """
    terms = query_terms(question)
    scored = store.similarity_search(question, fetch_k)
    seen = set()
    hits = []
    for doc, score in scored:
        if hangul_count(doc.page_content) < 8:
            continue
        row_i = doc.metadata.get("row")
        if row_i in seen:
            continue
        seen.add(row_i)
        name, explanation = store.entry_from_row(row_i)
        if not name:
            continue
        blob = name + "\n" + (explanation or "")
        matched = [term for term in terms if any(word in blob for word in variants(term))]
        if terms and not matched:
            continue
        in_name = sum(any(word in name for word in variants(term)) for term in terms)
        hits.append((len(matched), in_name, score, name, explanation or doc.page_content))
    hits.sort(key=lambda item: item[:3], reverse=True)
    picked = hits if k is None else hits[:k]
    return [(score, name, explanation) for _, _, score, name, explanation in picked]
