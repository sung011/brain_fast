"""
일반 API 라우터.

JSON을 주고받는 엔드포인트. 입력값이 잘못되면
exception_handlers.validation_exception_handler 가 422와 로그를 남긴다.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from db import get_db
from db import ping_db
from schemas.userSchemas import UserLogin
from services import userServices as user_service
from utils.password import hash_password, verify_password

router = APIRouter(tags=["api"])
templates = Jinja2Templates(directory="templates")


@router.get("/health/db")
def health_db():
    """PostgreSQL 연결 확인. 연결된 데이터베이스 이름을 돌려준다."""
    return {"ok": True, "database": ping_db()}


@router.get("/items/{item_id}")
async def read_item(item_id: int, q: str | None = None):
    """
    아이템 조회.
    - item_id: 경로의 숫자. 숫자가 아니면 422 입력값 오류가 나고 로그에 남는다.
    - q: 선택 쿼리 문자열. 예) /items/1?q=test
    """
    return {"item_id": item_id, "q": q}


@router.post("/login")
def login_post(body: UserLogin, db: Session = Depends(get_db)):
    user = user_service.login(db, body)

    # 개발 확인용 응답. 나중에 제거하세요.
    debug = {
        "plain_pw": body.user_pw,
        "hashed_pw": hash_password(body.user_pw),
        "db_user_pw": user.user_pw if user else None,
        "pw_match": (
            verify_password(body.user_pw, user.user_pw)
            if user and user.user_pw.startswith("$2")
            else None
        ),
    }
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "아이디 또는 비밀번호가 올바르지 않습니다.", **debug},
        )
    return {
        "ok": True,
        "idx": user.idx,
        "user_id": user.user_id,
        "user_name": user.user_name,
        "mb_level": user.mb_level,
        **debug,
    }
