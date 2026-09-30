"""
사용자 관련 API 라우터 (자리 잡기용).

prefix 없음. 나중에 마이페이지·프로필 등을 여기 추가하면 된다.

하는 일:
- GET /text   : 연결 확인용 Hello World (개발 테스트)
- GET /signup : 학습자 회원가입 화면(HTML). 화면만 있고 실제 가입 API는 아직 없다.
                ?preview=1 이면 설계 미리보기(단계 이동·오류 시뮬레이션·가상 완료 화면)를 켠다.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["user"])

# 실행 위치와 상관없이 프로젝트 루트의 templates/ 를 읽는다.
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")

_PREVIEW_VALUES = {"1", "true", "yes"}


# GET /signup → templates/signup.html
# 계정 저장·메일 발송은 하지 않는다. 실제 가입 연결 전에는 미리보기에서만 완료 화면을 보여 준다.
@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
def signup_page(request: Request, preview: str | None = None):
    return templates.TemplateResponse(
        request,
        "signup.html",
        {"preview": (preview or "").lower() in _PREVIEW_VALUES},
    )


@router.get("/text")
def me():
    """서버 연결 확인. {"message": "Hello World"} 를 돌려준다."""
    return {"message": "Hello World"}


@router.post("/example")
def example(body: dict[str, Any]):
    print(f"body : {body}")
    return body
