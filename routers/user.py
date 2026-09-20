"""
사용자 관련 API 라우터 (자리 잡기용).

prefix 없음. 나중에 마이페이지·프로필 등을 여기 추가하면 된다.

하는 일:
- GET /text : 연결 확인용 Hello World (개발 테스트)
"""

from fastapi import APIRouter

router = APIRouter(tags=["user"])


@router.get("/text")
def me():
    """서버 연결 확인. {"message": "Hello World"} 를 돌려준다."""
    return {"message": "Hello World"}
