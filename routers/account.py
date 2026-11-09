"""학습자 계정찾기·로그아웃 API. 화면은 학습자 Vue 프로젝트의 /find(아이디·비밀번호 찾기)가 맡는다. 기존 commonness 라우터와 분리한다.

- POST /find-id, /find-pw/verify, /find-pw : 이름·생년월일(+아이디) 대조 방식. 메일 인증이 아니다(설계 R-01 한계).
- POST /logout : 이 브라우저의 학습자 로그인(session["learner"])만 끝낸다. 관리자 세션·게스트 쿠키는 건드리지 않는다.
"""
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from db import get_db
from schemas.accountSchemas import FindId, FindPw, VerifyAccount
from services import accountServices as service

_log = logging.getLogger("account_api")


class AccountRoute(APIRoute):
    """계정찾기의 오류 응답·로그에 입력한 비밀번호·가입 정보를 포함하지 않는다."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                response = await handler(request)
            except RequestValidationError as exc:
                errors = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
                          for error in exc.errors()]
                response = JSONResponse(status_code=422, content={"ok": False, "detail": errors})
            except HTTPException as exc:
                response = JSONResponse(status_code=exc.status_code,
                                        content={"ok": False, "detail": exc.detail})
            except Exception as exc:
                # DB 오류 문구에는 입력값(이름·생년월일·아이디)이 매개변수로 섞일 수 있다. 응답과 로그에는 오류 종류만 남긴다.
                _log.error("account api failed error_type=%s", type(exc).__name__)
                response = JSONResponse(status_code=500,
                                        content={"ok": False, "detail": "서버 처리 중 문제가 생겼습니다."})
            response.headers["Cache-Control"] = "no-store"
            return response
        return safe_handler


router = APIRouter(tags=["account"], route_class=AccountRoute)
NOT_FOUND = "입력하신 정보와 일치하는 회원을 찾을 수 없습니다."
TOO_MANY = "시도 횟수가 너무 많습니다. 10분 후 다시 시도해 주세요."


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/find-id")
def find_id(body: FindId, request: Request, db: Session = Depends(get_db)):
    try:
        ids = service.find_id(db, body, client_ip(request))
    except service.TooManyAttempts as exc:
        raise HTTPException(429, TOO_MANY) from exc
    if not ids:
        raise HTTPException(404, NOT_FOUND)
    return {"ok": True, "user_id": ids[0] if len(ids) == 1 else None, "user_ids": ids}


@router.post("/find-pw/verify")
def verify_account(body: VerifyAccount, request: Request, db: Session = Depends(get_db)):
    try:
        found = service.verify_account(db, body, client_ip(request))
    except service.TooManyAttempts as exc:
        raise HTTPException(429, TOO_MANY) from exc
    if not found:
        raise HTTPException(404, NOT_FOUND)
    return {"ok": True}


@router.post("/find-pw")
def reset_password(body: FindPw, request: Request, db: Session = Depends(get_db)):
    try:
        changed = service.reset_password(db, body, client_ip(request))
    except service.TooManyAttempts as exc:
        raise HTTPException(429, TOO_MANY) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not changed:
        raise HTTPException(404, NOT_FOUND)
    return {"ok": True, "message": "비밀번호가 변경되었습니다."}



def _same_origin_request(request: Request) -> bool:
    """브라우저가 같은 출처(개발 프록시 포함)에서 보낸 POST 인가. 다른 사이트의 숨은 폼·외부 호출은 거절한다.

    Origin 이 반드시 있어야 하고, 그 scheme/host 가 이 요청이 받은 scheme/Host 와 같아야 한다. Sec-Fetch-Site: cross-site 도 거절한다.
    X-Forwarded-* 헤더는 믿지 않는다(개발 프록시는 changeOrigin:false 로 브라우저의 Host 를 그대로 전달한다).
    """
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return False
    origin = request.headers.get("origin")
    if not origin:
        return False
    parts = urlsplit(origin)
    host = request.headers.get("host", "")
    return bool(host) and parts.scheme == request.url.scheme and parts.netloc.lower() == host.lower()


@router.post("/logout")
def logout(request: Request):
    """이 브라우저의 학습자 로그인(session["learner"])만 끝낸다. 이미 없어도 200.

    다른 브라우저·이미 복사된 쿠키까지 끄는 전역 폐기가 아니다(서명 쿠키 방식의 한계). 관리자 세션(session["user"])과 게스트 쿠키는 그대로 둔다.
    DB 를 건드리지 않는다.
    """
    if not _same_origin_request(request):
        raise HTTPException(403, "허용되지 않은 요청입니다.")
    session = request.scope.get("session")
    if isinstance(session, dict):
        session.pop("learner", None)
    return {"ok": True}
