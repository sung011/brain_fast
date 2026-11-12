"""
사용자 관련 API 라우터 (자리 잡기용).

prefix 없음. 나중에 마이페이지·프로필 등을 여기 추가하면 된다.

하는 일:
- GET /text   : 연결 확인용 Hello World (개발 테스트)
- GET /signup : 학습자 회원가입 화면(HTML). 시연 화면이고, 저장은 POST /signup 이 한다.
                ?preview=1 이면 설계 미리보기(단계 이동·오류 시뮬레이션·가상 완료 화면)를 켠다.
- POST /signup : 학습자 가입(복구 B안: 비밀번호 찾기 질문 답 3개 필수). routers/learnerSignup.py 를 아래에서 include 한다.
- GET /me      : 마이페이지용 본인 프로필 + 최근 저장된 문제 제출 5건(읽기 전용). 회원은 서버 세션의 learner 로만 정한다.
- GET /me/stats : 마이페이지 학습 통계(시안 B). 같은 인증 규칙, 읽기 전용 집계 한 번. 날짜 기준은 서버가 정한다.
- GET /me/settings, PATCH /me/profile, POST /me/password, GET·PUT /me/recovery-questions : 내 정보 수정·비밀번호 변경·질문 답 등록(routers/accountSettings.py 를 아래에서 include).
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db import get_db
from routers.accountSettings import router as settings_router
from routers.learnerSignup import router as signup_router
from services import accountProfileServices as profile_service
from services import accountStatsServices as stats_service

router = APIRouter(tags=["user"])
router.include_router(settings_router)   # 내 정보 수정·비밀번호 변경(별도 파일, 라우트 클래스는 계정 API 정책 유지)
router.include_router(signup_router)     # POST /signup(별도 파일, 답 주소 안전 오류 정책). GET /signup 화면은 아래 그대로
_log = logging.getLogger("account_api")

# GET /me 에서 "다른 회원을 골라 보려는" 시도로 보는 쿼리 이름. 회원은 세션으로만 정하므로 이런 값이 오면 거절한다.
_IDENTITY_QUERY_KEYS = {"user_idx", "idx", "user_id", "uid", "id"}
_NO_STORE = {"Cache-Control": "no-store"}

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


@router.get("/me")
def learner_me(request: Request, db: Session = Depends(get_db)):
    """마이페이지용 본인 조회. 서버 세션의 learner 한 명의 프로필(5필드)과 최근 저장된 문제 제출 5건만 준다.

    - 회원 번호는 요청에서 받지 않는다. user_idx/idx/user_id 같은 쿼리로 다른 회원을 고르려 하면 400.
    - 세션 없음·형식 오류·탈퇴/없는 회원·관리자 세션만·게스트는 401, 관리자 승인 표시가 아니면 403.
    - 모든 응답(정상·오류)에 Cache-Control: no-store. DB 에 쓰지 않고 영상·저장소를 부르지 않는다.
    - 예상 못한 오류는 고정 문구 500 으로만 답하고 오류 종류만 기록한다(DB 오류 문구·값을 응답/로그에 싣지 않는다).
    """
    if any(key.lower() in _IDENTITY_QUERY_KEYS for key in request.query_params.keys()):
        return JSONResponse(status_code=400, content={"ok": False, "detail": "회원은 로그인 정보로만 확인합니다."}, headers=_NO_STORE)
    try:
        result = profile_service.get_me(db, request.scope.get("session"))
    except profile_service.NotAuthenticated:
        return JSONResponse(status_code=401, content={"ok": False, "detail": "로그인이 필요합니다."}, headers=_NO_STORE)
    except profile_service.NotApproved:
        return JSONResponse(status_code=403, content={"ok": False, "detail": "관리자 승인 후 사용할 수 있습니다."}, headers=_NO_STORE)
    except Exception as exc:
        _log.error("learner me failed error_type=%s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"ok": False, "detail": "서버 처리 중 문제가 생겼습니다."}, headers=_NO_STORE)
    return JSONResponse(content=result.model_dump(), headers=_NO_STORE)


@router.get("/me/stats")
def learner_me_stats(request: Request, db: Session = Depends(get_db)):
    """마이페이지 학습 통계(시안 B). GET /me 와 같은 학습자 인증·같은 오류 규칙을 쓰고 DB 를 읽기만 한다.

    - 회원·기간·시간대·날짜는 요청에서 받지 않는다(user_idx/idx/user_id/uid/id 쿼리는 400). 날짜 기준은 서버가 정한다.
    - 세션 없음·형식 오류·탈퇴/없는 회원·관리자 세션만·게스트는 401, 관리자 승인 표시가 아니면 403. 모든 응답에 Cache-Control: no-store.
    - 예상 못한 오류(DB 오류·응답 검증 실패 포함)는 고정 문구 500 으로만 답하고 오류 종류만 기록한다.
    """
    if any(key.lower() in _IDENTITY_QUERY_KEYS for key in request.query_params.keys()):
        return JSONResponse(status_code=400, content={"ok": False, "detail": "회원은 로그인 정보로만 확인합니다."}, headers=_NO_STORE)
    try:
        result = stats_service.get_stats(db, request.scope.get("session"))
    except profile_service.NotAuthenticated:
        return JSONResponse(status_code=401, content={"ok": False, "detail": "로그인이 필요합니다."}, headers=_NO_STORE)
    except profile_service.NotApproved:
        return JSONResponse(status_code=403, content={"ok": False, "detail": "관리자 승인 후 사용할 수 있습니다."}, headers=_NO_STORE)
    except Exception as exc:
        _log.error("learner stats failed error_type=%s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"ok": False, "detail": "서버 처리 중 문제가 생겼습니다."}, headers=_NO_STORE)
    return JSONResponse(content=result.model_dump(mode="json"), headers=_NO_STORE)


@router.get("/text")
def me():
    """서버 연결 확인. {"message": "Hello World"} 를 돌려준다."""
    return {"message": "Hello World"}


@router.post("/example")
def example(body: dict[str, Any]):
    print(f"body : {body}")
    return body
