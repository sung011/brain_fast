"""
의학용어 검색 API.

prefix 없음. 주소: GET /glossary/search?q=두통과 구토

GET /glossary 는 medical_terms 테이블을 부위·영상 종류로 조회한다.
이 주소는 질문 문장으로 로컬 색인을 검색하고, 응답 항목 칸은 /glossary 와 같다.
q 를 비우거나 생략하면 색인에서 용어와 병을 무작위로 3500건 돌려준다.
q 가 있으면 맞는 항목을 모두 돌려준다.
색인이 없으면 503 이고, 다른 API 는 그대로다.
결과는 학습용 검색이고 진단이 아니다.
"""

from fastapi import APIRouter, HTTPException, Query, status

from schemas.ragSchemas import RagGlossaryOut
from services import ragServices as rag_service

router = APIRouter(tags=["glossary-search"])


@router.get("/glossary/search", response_model=RagGlossaryOut)
def glossary_search(
        q: str | None = Query(
            default=None,
            max_length=300,
            description="증상이나 병명. 비우면 색인에서 3500건을 무작위로 돌려준다.",
        ),
):
    """
    질문으로 용어와 관련 병을 찾는다. 질문이 없으면 색인에서 3500건을 무작위로 돌려준다.

    예) /glossary/search?q=두통과 구토가 있고 허리통증이 있어요
    예) /glossary/search
    items 의 category 는 term(용어) 또는 disease(관련 병)이다.
    """
    try:
        result = rag_service.search_glossary(q)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    except rag_service.RagUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return {"ok": True, **result}
