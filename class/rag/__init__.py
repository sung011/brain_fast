"""
의학용어 검색.

패키지 폴더 이름이 class 라서 `from class.rag import ...` 는 쓸 수 없다.

    import importlib
    rag = importlib.import_module("class.rag")
    rag.search("두통과 구토가 있고 허리통증이 있어요")

search 와 plain_answer 는 색인의 용어 글(index.pkl)만 읽는다.
vector_hits 만 벡터 파일과 임베딩 라이브러리가 필요하다.
질문 해석(query_terms)은 색인 없이 동작한다.
결과는 학습용 검색이고 진단이 아니다.
"""
from .query import query_terms
from .retrieve import find_answers, list_entries, plain_answer, search, vector_hits
from .store import RagIndexError

__all__ = [
    "RagIndexError",
    "find_answers",
    "list_entries",
    "plain_answer",
    "query_terms",
    "search",
    "vector_hits",
]
