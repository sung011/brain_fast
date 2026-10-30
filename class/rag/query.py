"""질문 문장을 검색어 목록으로 바꾼다."""
import re

from .constants import FILLERS, JOSA, SYNONYMS


def query_terms(question: str) -> list[str]:
    """쉼표로 나열한 단어도 각각 검색어가 되게 한다. 있어요를 있어보다 먼저 지운다."""
    terms: list[str] = []
    for token in re.findall(r"[가-힣A-Za-z0-9]+", question or ""):
        if token in FILLERS:
            continue
        part = token
        for end in JOSA:
            if len(part) > 2 and part.endswith(end):
                part = part[:-1]
                break
        if not part or part in FILLERS or part in terms:
            continue
        # 한 글자는 질문 전체가 그 글자일 때만 남긴다. 뇌는 남고, 있어요의 요는 남지 않는다.
        if len(part) < 2 and token != (question or "").strip():
            continue
        terms.append(part)
    return terms


def variants(term: str) -> tuple[str, ...]:
    return SYNONYMS.get(term, (term,))
