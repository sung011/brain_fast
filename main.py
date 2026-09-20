"""
FastAPI 앱의 시작 파일 (진입점).

서버를 켜면 이 파일이 먼저 실행됩니다.

하는 일 요약:
1. 서버 시작 시: 오류 로그 준비, DB 연결 대기, 7일 지난 로그 폴더 삭제
2. 서버가 켜져 있는 동안: 1시간마다 오래된 로그를 다시 검사해 삭제
3. 오류가 나면: exception_handlers 가 logs/날짜/error.log 에 내용을 남김
4. 주소 연결:
   - /          → 화면 공유 분석 페이지
   - /admin     → 관리자 페이지
   - /api/...   → JSON API (commonness, user, brain)
   - /assets/... → CSS, JS, 이미지
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
from routers import admin, commonness, brain, user

# 오래된 로그를 얼마나 자주 검사할지 (초 단위). 3600초 = 1시간.
CLEANUP_INTERVAL_SECONDS = 60 * 60

# 이 파일(main.py)이 있는 폴더 = 프로젝트 루트
BASE_DIR = Path(__file__).resolve().parent

# HTML 템플릿을 읽어 오는 설정. templates/ 안의 .html 을 쓴다.
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _maybe_load_brain_models() -> None:
    """
    서버가 켜질 때 AI 모델을 미리 불러올지 결정한다.

    - LOAD_MODELS_ON_STARTUP=1 이면 앙상블 모델을 미리 로드한다.
    - LOAD_MEDGEMMA_ON_STARTUP 이면 MedGemma도 미리 로드한다.
    - 로드에 실패해도 서버(관리자 페이지 등)는 그대로 켠다.
    """
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
    """
    NAS(파일 저장소) 로그인을 미리 해 둔다.

    첫 업로드 때 로그인하느라 느려지지 않게, 서버 시작 직후에
    백그라운드에서 연결·SID를 미리 받아 둔다.
    NAS 설정(url/user/password)이 없으면 아무 것도 하지 않는다.
    """
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
    서버가 '켜질 때'와 '꺼질 때' 할 일을 정한다.

    켜질 때:
    - 로그 설정
    - DB 준비될 때까지 대기
    - 7일 지난 로그 폴더 삭제
    - (설정되어 있으면) AI 모델·NAS 미리 준비
    - 1시간마다 오래된 로그를 지우는 백그라운드 작업 시작

    꺼질 때:
    - 그 백그라운드 작업을 멈추고 끝낸다

    왜 필요한가?
    서버를 며칠씩 켜 두면, 요청이 없어도 날짜가 지나간 로그가
    쌓이므로, 주기적으로 지워 주기 위함이다.
    """
    setup_logging()
    wait_for_db()
    purge_old_log_folders()
    _maybe_load_brain_models()
    _maybe_warmup_nas()

    # stop.set() 이 호출되면 cleanup_loop 가 종료된다.
    stop = asyncio.Event()

    async def cleanup_loop() -> None:
        """
        1시간마다 한 번씩, 7일 지난 logs/날짜 폴더를 삭제한다.
        서버 종료 신호(stop)가 오면 while 을 빠져나와 끝낸다.
        """
        while True:
            try:
                # 1시간 동안 stop 신호가 없으면 TimeoutError → 그때 로그 정리.
                # stop 신호가 오면(서버 종료) 여기서 return 한다.
                await asyncio.wait_for(stop.wait(), timeout=CLEANUP_INTERVAL_SECONDS)
                return
            except asyncio.TimeoutError:
                purge_old_log_folders()

    task = asyncio.create_task(cleanup_loop())
    yield  # 이 줄 이후부터 실제 HTTP 요청을 받기 시작한다.
    stop.set()  # 종료 신호
    await task  # cleanup_loop 가 끝날 때까지 기다림


app = FastAPI(lifespan=lifespan)


@app.get("/")
def screen_analyze_page(request: Request):
    """
    메인 화면(/).
    화면 공유 → 관심 영역(ROI) 선택 → 분석하는 MediLens 화면을 연다.
    """
    return templates.TemplateResponse(
        request,
        "screen_analyze.html",
        {"request": request},
    )


# --- 미들웨어 (요청이 라우터에 닿기 전에 거치는 공통 처리) ---

# 로그인 세션: 브라우저 쿠키에 로그인 상태를 저장한다.
# 코드에서는 request.session["키"] 로 읽고 쓴다.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="brain_fast_session",
    same_site="lax",
    https_only=False,
)

# CORS: 다른 주소(예: Desktop/팀플/index.html)의 웹페이지에서도
# 이 서버 API를 호출할 수 있게 허용한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 예외(오류) 핸들러: 종류별로 로그에 남기고 응답을 돌려준다 ---
app.add_exception_handler(RequestValidationError, validation_exception_handler)  # 입력값 오류 → 422
app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # 의도적으로 올린 HTTP 오류
app.add_exception_handler(Exception, unhandled_exception_handler)  # 그 외 예상 못 한 서버 오류 → 500

# --- 라우터 연결: 주소(URL)와 처리 코드를 이어 붙인다 ---
# 자세한 기능 목록은 각 routers/*.py 파일 맨 위 주석을 본다.

# admin: 관리자 HTML (회원·스터디·팝업·문의·리뷰·NAS). /admin/...
# include_in_schema=False → Swagger(/docs)에는 안 보임
app.include_router(admin.router, include_in_schema=False)

# commonness: 학습자 JSON API (로그인·Q&A·학습·팝업·용어사전)
app.include_router(commonness.router)

# user: 사용자 API 자리 (현재 GET /text 테스트만)
app.include_router(user.router)

# brain: 화면 공유 ROI 분석·판독문. 앞에 /api/v1 을 붙임
app.include_router(brain.router, prefix="/api/v1", tags=["brain"])

# --- 정적 파일: CSS / JS / 이미지 ---
# HTML에서는 /assets/css/styles.css 처럼 경로를 쓴다.
# 라우터보다 뒤에 붙여야 /, /admin 같은 주소와 충돌하지 않는다.
ASSETS_DIR = BASE_DIR / "assets"
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
