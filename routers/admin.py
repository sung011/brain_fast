"""
관리자 웹 페이지 라우터.

router 의 prefix="/admin" 이므로
이 파일의 "/" 는 실제 주소 /admin 이 된다.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db import get_db
from schemas.userSchemas import UserCreate, UserLogin, UserOut, UserUpdate
from services import userServices as user_service
from utils.adminAuth import admin_session_guard
from utils.password import hash_password, verify_password
from utils.renderHelper import render_with_layout
from utils.user_sse import user_events

# tags: /docs 에 묶어서 보여 줄 이름. 지금은 include_in_schema=False 라 문서에는 안 나온다.
# dependencies: 로그인/로그아웃 제외한 /admin/* 은 세션 필요.
router = APIRouter(
    tags=["admin"],
    prefix="/admin",
    dependencies=[Depends(admin_session_guard)],
)
# HTML 템플릿 폴더. templates/login.html 을 여기서 읽는다.
# CSS/JS/이미지는 main.py 에서 /assets 로 연결한다.
templates = Jinja2Templates(directory="templates/admin")


# 관리자 로그인 화면
# - GET /admin/login → templates/admin/login.html
@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"name": "FastAPI"},
    )


@router.post("/login")
def login_post(request: Request, body: UserLogin, db: Session = Depends(get_db)):
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

    # 로그인 성공 시 세션에 사용자 정보 저장 (비밀번호는 저장하지 않음)
    request.session["user"] = {
        "idx": user.idx,
        "user_id": user.user_id,
        "user_name": user.user_name,
        "mb_level": user.mb_level,
    }

    return {
        "ok": True,
        "idx": user.idx,
        "user_id": user.user_id,
        "user_name": user.user_name,
        "mb_level": user.mb_level,
        **debug,
    }


@router.get("/logout")
def logout(request: Request):
    """세션을 비우고 로그인 화면으로 보낸다."""
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)


# 관리자 메인 화면
# - GET /admin → index.html 본문을 그린 뒤 partials/layout_adm.html 레이아웃에 넣는다
@router.get("/")
def index_page(request: Request):
    return render_with_layout(
        request,
        templates,
        "index",
        {
            "title": "Dashboard",
            "user": request.session.get("user")
        },
    )


@router.get("/user")
def user_page(request: Request, partial: bool = False):
    """
    회원 목록.
    - /admin/user : 레이아웃 포함 (주소로 직접 열 때)
    - /admin/user?partial=1 : 본문만 (사이드바 탭에서 fetch 할 때)
    """
    data = {"title": "회원 관리"}
    if partial:
        return templates.TemplateResponse(
            request=request,
            name="user.html",
            context=data,
        )
    return render_with_layout(request, templates, "user", data)


@router.get("/users_all", response_model=list[UserOut])
def users_all(db: Session = Depends(get_db)):
    return user_service.user_all_data(db)


@router.get("/users/stream")
async def users_stream(request: Request):
    """회원 목록 변경을 실시간으로 알린다 (SSE)."""

    async def event_generator():
        queue = await user_events.subscribe()
        try:
            yield user_events.format_sse({"event": "connected", "data": {"ok": True}})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield user_events.format_sse(message)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await user_events.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/user/signup")
def user_signup_page(request: Request, partial: bool = False):
    """
    회원가입 화면 (입력 폼만. 실제 저장은 POST /user/signup).

    - /admin/user/signup : 사이드바·헤더 포함 전체 페이지
    - /admin/user/signup?partial=1 : 본문 HTML만 (회원 목록의 '회원가입' 탭)
    """
    data = {"title": "회원가입"}
    if partial:
        return templates.TemplateResponse(
            request=request,
            name="user_signup.html",
            context=data,
        )
    return render_with_layout(request, templates, "user_signup", data)


@router.get("/user/check-id")
def user_check_id(user_id: str, db: Session = Depends(get_db)):
    """
    아이디(이메일) 중복 확인.
    회원가입 폼에서 아이디 칸 blur / 제출 직전에 호출한다.
    available=true 이면 가입 가능.
    """
    user_id = user_id.strip()
    if not user_id:
        return {"ok": False, "available": False, "message": "아이디를 입력해주세요."}

    if user_service.user_id_exists(db, user_id):
        return {
            "ok": True,
            "available": False,
            "message": "이미 사용 중인 아이디입니다.",
        }

    return {"ok": True, "available": True}


@router.post("/user/signup")
async def user_signup_post(body: UserCreate, db: Session = Depends(get_db)):
    """
    회원 등록.
    비밀번호는 서비스에서 해시한다. 아이디가 이미 있으면 409.
    성공 시 SSE(user_created)로 목록 탭을 갱신한다.
    """
    user = user_service.create_user(db, body)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "이미 사용 중인 이메일(아이디)입니다."},
        )

    payload = {
        "idx": user.idx,
        "user_id": user.user_id,
        "user_name": user.user_name,
    }
    # 열려 있는 회원 목록이 있으면 새로고침 없이 행이 추가된다.
    await user_events.publish("user_created", payload)
    return {
        "ok": True,
        **payload,
    }


@router.get("/user/{idx}", response_model=UserOut)
def user_get(idx: int, db: Session = Depends(get_db)):
    user = user_service.get_user(db, idx)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "회원을 찾을 수 없습니다."},
        )
    return user


@router.put("/user/{idx}")
async def user_update(idx: int, body: UserUpdate, db: Session = Depends(get_db)):
    try:
        user = user_service.update_user(db, idx, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": str(exc)},
        ) from exc

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "회원을 찾을 수 없습니다."},
        )

    payload = {
        "idx": user.idx,
        "user_id": user.user_id,
        "user_name": user.user_name,
        "mb_level": user.mb_level,
    }
    await user_events.publish("user_updated", payload)
    return {
        "ok": True,
        **payload,
    }


@router.delete("/user/{idx}")
async def user_delete(idx: int, db: Session = Depends(get_db)):
    user = user_service.delete_user(db, idx)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "회원을 찾을 수 없습니다."},
        )

    payload = {
        "idx": user.idx,
        "user_id": user.user_id,
    }
    await user_events.publish("user_deleted", payload)
    return {
        "ok": True,
        **payload,
    }
