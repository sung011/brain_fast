"""
FastAPI 앱 진입점.

하는 일:
- 서버가 켜질 때 오류 로그를 설정하고, 7일 지난 로그 폴더를 지운다.
- 한 시간마다 오래된 로그를 다시 검사한다.
- 오류가 나면 exception_handlers 가 내용을 로그에 남긴다.
- /admin 은 관리자 페이지, 나머지 API 라우터는 그대로 연결한다.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from config import settings
from db import wait_for_db
from exception_handlers import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from logging_setup import purge_old_log_folders, setup_logging
from routers import admin, commonness, brain

# 오래된 로그 폴더를 다시 검사하는 간격(초). 1시간마다 한 번.
CLEANUP_INTERVAL_SECONDS = 60 * 60

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _maybe_load_brain_models() -> None:
    """LOAD_MODELS_ON_STARTUP=1 일 때만 앙상블을 미리 로드한다. 실패해도 관리자 페이지는 켠다."""
    from importlib import import_module

    brain_config = import_module("class.brain.config")
    if not brain_config.LOAD_MODELS_ON_STARTUP:
        return
    brain = import_module("class.brain")
    try:
        brain.ensemble_service.load()
    except Exception:
        pass
    if brain_config.LOAD_MEDGEMMA_ON_STARTUP:
        try:
            brain.medgemma_service.load()
        except Exception:
            pass


def _maybe_warmup_nas() -> None:
    """NAS SID·연결을 미리 열어 첫 업로드 지연을 줄인다."""
    if not settings.nas_url or not settings.nas_user or not settings.nas_password:
        return

    async def _run() -> None:
        try:
            from services.nasServices import nas_service

            client = await nas_service._get_client()
            await nas_service._ensure_sid(client, force=True)
        except Exception:
            pass

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    앱 수명 주기.
    - 시작: 로거 준비 + 오래된 날짜 폴더 삭제 + 백그라운드 정리 작업 시작
    - 종료: 정리 작업을 멈추고 끝낸다
    서버가 며칠씩 켜져 있어도, 요청이 없어도 7일 지난 로그가 지워지게 한다.
    """
    setup_logging()
    wait_for_db()
    purge_old_log_folders()
    _maybe_load_brain_models()
    _maybe_warmup_nas()
    stop = asyncio.Event()

    async def cleanup_loop() -> None:
        """1시간마다 7일 지난 logs/날짜 폴더를 삭제한다. stop 이 켜지면 종료한다."""
        while True:
            try:
                # timeout 동안 stop 신호가 없으면 TimeoutError → 그때 한 번 정리한다.
                await asyncio.wait_for(stop.wait(), timeout=CLEANUP_INTERVAL_SECONDS)
                return
            except asyncio.TimeoutError:
                purge_old_log_folders()

    task = asyncio.create_task(cleanup_loop())
    yield  # 여기부터 실제 요청을 받기 시작한다.
    stop.set()
    await task


app = FastAPI(lifespan=lifespan)


@app.get("/")
def screen_analyze_page(request: Request):
    """화면 공유 → ROI → 분석. MediLens 화면을 그대로 연다."""
    return templates.TemplateResponse(
        request,
        "screen_analyze.html",
        {"request": request},
    )


# 로그인 세션(쿠키). request.session 으로 읽고 쓴다. (바깥쪽 미들웨어)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="brain_fast_session",
    same_site="lax",
    https_only=False,
)

# Desktop/팀플/index.html 처럼 다른 주소에서 API를 호출할 수 있게 한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 오류 종류별로 핸들러를 연결한다. 여기서 로그 파일에 내용이 기록된다.
app.add_exception_handler(RequestValidationError, validation_exception_handler)  # 입력값 오류(422)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # HTTPException
app.add_exception_handler(Exception, unhandled_exception_handler)  # 그 외 서버 예외(500)

# 관리자 페이지는 /admin 으로만 들어온다. Swagger(/docs)에는 숨긴다.
# prefix 는 admin.router 에 이미 있음.
# app.include_router(admin.router, include_in_schema=False)
app.include_router(admin.router)

# API 라우터. 경로 예: /items/{item_id}
app.include_router(commonness.router)

# 화면 공유 ROI 분석 (MediLens와 동일 경로)
app.include_router(brain.router, prefix="/api/v1", tags=["brain"])

# CSS/JS/이미지 정적 파일. HTML에서는 /assets/css/styles.css 처럼 사용한다.
# 라우터보다 뒤에 붙여야 /, /admin 같은 경로를 가리지 않는다.
ASSETS_DIR = BASE_DIR / "assets"
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
