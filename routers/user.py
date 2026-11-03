"""
사용자 관련 API 라우터 (자리 잡기용).

prefix 없음. 나중에 마이페이지·프로필 등을 여기 추가하면 된다.

하는 일:
- GET /text   : 연결 확인용 Hello World (개발 테스트)
- GET /signup : 학습자 회원가입 화면(HTML). 시연 화면이고, 저장은 POST /signup 이 한다.
                ?preview=1 이면 설계 미리보기(단계 이동·오류 시뮬레이션·가상 완료 화면)를 켠다.
- POST /signup : 학습자 가입. 이메일·비밀번호·닉네임·생년월일·학교·전공. 관리자 세션은 쓰지 않는다.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db import get_db
from schemas.userSchemas import LearnerSignup, UserCreate
from services import userServices as user_service

router = APIRouter(tags=["user"])

# 실행 위치와 상관없이 프로젝트 루트의 templates/ 를 읽는다.
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")

_PREVIEW_VALUES = {"1", "true", "yes"}


# GET /signup → templates/signup.html
# 이 HTML 은 시연 화면이다. 저장은 POST /signup 이 한다.
@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
def signup_page(request: Request, preview: str | None = None):
    return templates.TemplateResponse(
        request,
        "signup.html",
        {"preview": (preview or "").lower() in _PREVIEW_VALUES},
    )


@router.post("/signup")
def learner_signup(body: LearnerSignup, db: Session = Depends(get_db)):
    """
    학습자 가입. 관리자 세션 없이 학교·전공·생년월일을 저장한다.
    비밀번호는 서비스에서 해시한다. 이미 있는 아이디면 409.
    응답에는 비밀번호와 해시를 넣지 않는다.
    """
    user = user_service.create_user(
        db,
        UserCreate(
            user_id=body.user_id,
            user_pw=body.user_pw,
            user_name=body.user_name,
            user_birth=body.user_birth,
            user_un=body.user_un,
            user_sp=body.user_sp,
        ),
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "이미 사용 중인 아이디입니다."},
        )
    return {
        "ok": True,
        "idx": user.idx,
        "user_id": user.user_id,
        "user_name": user.user_name,
    }


@router.get("/text")
def me():
    """서버 연결 확인. {"message": "Hello World"} 를 돌려준다."""
    return {"message": "Hello World"}


@router.post("/example")
def example(body: dict[str, Any]):
    print(f"body : {body}")
    return body
