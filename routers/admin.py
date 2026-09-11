"""
관리자 웹 페이지 라우터.

router 의 prefix="/admin" 이므로
이 파일의 "/" 는 실제 주소 /admin 이 된다.
"""

import asyncio
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from config import settings
from db import get_db
from schemas.analyzeSchemas import NasHealthResponse, NasUploadResponse
from schemas.studySchemas import (
    ST_MODAL_LABELS,
    ST_PART_LABELS,
    StudyCreateResult,
    StudyOut,
    StudyUpdateResult,
    build_remote_dir,
)
from schemas.userSchemas import UserCreate, UserLogin, UserOut, UserUpdate
from services import nasServices as nas_service_mod
from services import studyServices as study_service
from services import userServices as user_service
from utils.adminAuth import admin_session_guard
from utils.password import hash_password, verify_password
from utils.renderHelper import render_with_layout
from utils.study_sse import study_events
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


# ---------------------------------------------------------------------------
# 학습(study) + NAS
# ---------------------------------------------------------------------------


@router.get("/study")
def study_page(request: Request, partial: bool = False):
    """학습 목록."""
    data = {
        "title": "학습 관리",
        "part_labels": ST_PART_LABELS,
        "modal_labels": ST_MODAL_LABELS,
        "nas_base_path": settings.nas_base_path or "/stylesheets/assets",
    }
    if partial:
        return templates.TemplateResponse(
            request=request,
            name="study.html",
            context=data,
        )
    return render_with_layout(request, templates, "study", data)


@router.get("/study/create")
def study_create_page(request: Request, partial: bool = False):
    """학습 등록 폼 (부위·모달리티·병명·이미지·NAS 경로)."""
    data = {
        "title": "학습 등록",
        "part_labels": ST_PART_LABELS,
        "modal_labels": ST_MODAL_LABELS,
        "nas_base_path": settings.nas_base_path or "/stylesheets/assets",
    }
    if partial:
        return templates.TemplateResponse(
            request=request,
            name="study_create.html",
            context=data,
        )
    return render_with_layout(request, templates, "study_create", data)


def _public_image_url(st_image: str | None) -> str | None:
    """DB st_image → 브라우저에서 볼 수 있는 절대 URL."""
    if not st_image:
        return None
    path = st_image if st_image.startswith("/") else f"/{st_image}"
    base = (settings.nas_public_base_url or "").rstrip("/")
    if not base:
        return path
    return f"{base}{path}"


@router.get("/study/{idx}")
def study_detail_page(idx: int, request: Request, partial: bool = False):
    """학습 상세/수정 화면."""
    data = {
        "title": f"학습 상세 #{idx}",
        "study_idx": idx,
        "part_labels": ST_PART_LABELS,
        "modal_labels": ST_MODAL_LABELS,
        "nas_base_path": settings.nas_base_path or "/stylesheets/assets",
        "nas_public_base_url": (
            settings.nas_public_base_url or "https://olleh7531.synology.me/mu_shop/public"
        ).rstrip("/"),
    }
    if partial:
        return templates.TemplateResponse(
            request=request,
            name="study_detail.html",
            context=data,
        )
    return render_with_layout(request, templates, "study_detail", data)


@router.get("/studies_all", response_model=list[StudyOut])
def studies_all(db: Session = Depends(get_db)):
    return study_service.list_studies(db)


@router.get("/studies/stream")
async def studies_stream(request: Request):
    """학습 목록 변경을 실시간으로 알린다 (SSE)."""

    async def event_generator():
        queue = await study_events.subscribe()
        try:
            yield study_events.format_sse({"event": "connected", "data": {"ok": True}})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield study_events.format_sse(message)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await study_events.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/studies/{idx}", response_model=StudyOut)
def study_get(idx: int, db: Session = Depends(get_db)):
    row = study_service.get_study(db, idx)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "학습 데이터를 찾을 수 없습니다."},
        )
    return row


@router.get("/nas/health", response_model=NasHealthResponse)
async def nas_health() -> NasHealthResponse:
    """Synology NAS 로그인 가능 여부."""
    result = await nas_service_mod.nas_service.health()
    return NasHealthResponse(**result)


def _to_nas_upload_dir(remote_dir: str) -> str:
    """
    입력 경로 → File Station 업로드 폴더.
    /stylesheets/assets  → /web/mu_shop/public/stylesheets/assets
    /web/mu_shop/public/... 는 그대로 사용.
    """
    path = (remote_dir or "").strip().replace("\\", "/") or "/stylesheets/assets"
    if not path.startswith("/"):
        path = f"/{path}"
    root = (settings.nas_public_root or "/web/mu_shop/public").rstrip("/")
    if path.startswith(root + "/") or path == root:
        return path.rstrip("/") or root
    return f"{root}{path}".rstrip("/")


def _to_public_image_path(remote_path: str) -> str:
    """
    NAS 전체 경로 → DB/웹용 상대 경로.
    /web/mu_shop/public/stylesheets/assets/a.png
      → /stylesheets/assets/a.png
    """
    path = (remote_path or "").replace("\\", "/")
    root = (settings.nas_public_root or "/web/mu_shop/public").rstrip("/")
    if path.startswith(root + "/"):
        return path[len(root) :]
    if path.startswith(root):
        rest = path[len(root) :]
        return rest if rest.startswith("/") else f"/{rest}" if rest else "/"
    return path if path.startswith("/") else f"/{path}"


async def _upload_and_save_study(
    db: Session,
    *,
    file: UploadFile,
    st_part: str,
    st_modal: str,
    st_disease: str,
    remote_dir: str,
) -> StudyCreateResult:
    """이미지를 NAS에 올린 뒤 study 행을 만든다."""
    svc = nas_service_mod.nas_service
    if not svc.enabled:
        raise HTTPException(
            status_code=503,
            detail="NAS가 설정되지 않았습니다. .env 에 NAS_URL, NAS_USER, NAS_PASSWORD 를 넣으세요.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="이미지 파일이 비어 있습니다.")

    # 부위·영상 종류로 경로 자동 결정 (프론트 remote_dir 보다 우선)
    auto_dir = build_remote_dir(st_part, st_modal)
    dest = _to_nas_upload_dir(auto_dir)
    safe_name = (file.filename or "study.bin").replace("\\", "/").split("/")[-1]
    upload_name = f"{uuid.uuid4().hex[:10]}_{safe_name}"

    try:
        uploaded = await svc.upload_bytes(data, upload_name, remote_dir=dest)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"NAS 업로드 실패: {exc}") from exc

    remote_path = uploaded.get("remote_path") or f"{dest}/{upload_name}"
    # DB에는 /web/mu_shop/public 을 뺀 웹 경로만 저장
    st_image = _to_public_image_path(remote_path)
    try:
        row = study_service.create_study(
            db,
            st_part=st_part,
            st_modal=st_modal,
            st_disease=st_disease,
            st_image=st_image,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": str(exc)},
        ) from exc

    await study_events.publish(
        "study_created",
        {
            "idx": row.idx,
            "st_part": row.st_part,
            "st_modal": row.st_modal,
            "st_disease": row.st_disease,
            "st_image": row.st_image,
        },
    )

    return StudyCreateResult(
        ok=True,
        idx=row.idx,
        st_part=row.st_part,
        st_modal=row.st_modal,
        st_disease=row.st_disease,
        st_image=row.st_image,
        remote_path=remote_path,
    )


@router.post("/nas/upload", response_model=NasUploadResponse)
async def nas_upload(
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    st_part: str = Form(..., description="학습 부위 — 1:뇌 2:흉부 3:복부 4:무릎"),
    st_modal: str = Form(..., description="영상 종류 — 1:X-ray 2:CT 3:MRI"),
    st_disease: str = Form(..., description="병명 (최대 30자)"),
    remote_dir: str = Form(
        default="/stylesheets/assets",
        description="웹 상대 저장 폴더. 기본값 /stylesheets/assets (NAS에는 /web/mu_shop/public 이 앞에 붙음)",
    ),
) -> NasUploadResponse:
    """
    NAS 업로드 + study 테이블 저장.

    Swagger에서 st_part / st_modal / st_disease / file 을 입력한다.
    """
    result = await _upload_and_save_study(
        db,
        file=file,
        st_part=st_part,
        st_modal=st_modal,
        st_disease=st_disease,
        remote_dir=remote_dir,
    )
    return NasUploadResponse(
        ok=True,
        remote_path=result.remote_path,
        filename=(result.remote_path or "").rsplit("/", 1)[-1] or None,
        bytes=None,
        idx=result.idx,
        st_part=result.st_part,
        st_modal=result.st_modal,
        st_disease=result.st_disease,
        st_image=result.st_image,
    )


@router.post("/study", response_model=StudyCreateResult)
async def study_create(
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    st_part: str = Form(..., description="1:뇌 2:흉부 3:복부 4:무릎"),
    st_modal: str = Form(..., description="1:X-ray 2:CT 3:MRI"),
    st_disease: str = Form(..., description="병명"),
    remote_dir: str = Form(
        default="/stylesheets/assets",
        description="웹 상대 저장 폴더. 기본값 /stylesheets/assets",
    ),
) -> StudyCreateResult:
    """학습 등록: 이미지를 NAS에 올린 뒤 study 테이블에 경로·메타를 저장한다."""
    return await _upload_and_save_study(
        db,
        file=file,
        st_part=st_part,
        st_modal=st_modal,
        st_disease=st_disease,
        remote_dir=remote_dir,
    )


@router.put("/study/{idx}", response_model=StudyUpdateResult)
async def study_update(
    idx: int,
    db: Session = Depends(get_db),
    st_part: str = Form(..., description="1:뇌 2:흉부 3:복부 4:무릎"),
    st_modal: str = Form(..., description="1:X-ray 2:CT 3:MRI"),
    st_disease: str = Form(..., description="병명"),
    file: UploadFile | None = File(None),
) -> StudyUpdateResult:
    """학습 수정. 이미지 파일이 있으면 NAS에 새로 올린 뒤 경로를 갱신한다."""
    existing = study_service.get_study(db, idx)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "학습 데이터를 찾을 수 없습니다."},
        )

    new_image: str | None = None
    remote_path: str | None = None
    has_file = bool(file and file.filename)
    if has_file and file is not None:
        svc = nas_service_mod.nas_service
        if not svc.enabled:
            raise HTTPException(
                status_code=503,
                detail="NAS가 설정되지 않았습니다. .env 에 NAS_URL, NAS_USER, NAS_PASSWORD 를 넣으세요.",
            )
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="이미지 파일이 비어 있습니다.")
        dest = _to_nas_upload_dir(build_remote_dir(st_part, st_modal))
        safe_name = (file.filename or "study.bin").replace("\\", "/").split("/")[-1]
        upload_name = f"{uuid.uuid4().hex[:10]}_{safe_name}"
        try:
            uploaded = await svc.upload_bytes(data, upload_name, remote_dir=dest)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"NAS 업로드 실패: {exc}") from exc
        remote_path = uploaded.get("remote_path") or f"{dest}/{upload_name}"
        new_image = _to_public_image_path(remote_path)

    try:
        row = study_service.update_study(
            db,
            idx,
            st_part=st_part,
            st_modal=st_modal,
            st_disease=st_disease,
            st_image=new_image,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": str(exc)},
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "학습 데이터를 찾을 수 없습니다."},
        )

    await study_events.publish(
        "study_updated",
        {
            "idx": row.idx,
            "st_part": row.st_part,
            "st_modal": row.st_modal,
            "st_disease": row.st_disease,
            "st_image": row.st_image,
        },
    )
    return StudyUpdateResult(
        ok=True,
        idx=row.idx,
        st_part=row.st_part,
        st_modal=row.st_modal,
        st_disease=row.st_disease,
        st_image=row.st_image,
        remote_path=remote_path,
    )


@router.delete("/study/{idx}")
async def study_delete(idx: int, db: Session = Depends(get_db)):
    row = study_service.delete_study(db, idx)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "학습 데이터를 찾을 수 없습니다."},
        )
    await study_events.publish(
        "study_deleted",
        {"idx": row.idx, "st_image": row.st_image},
    )
    return {"ok": True, "idx": row.idx}
