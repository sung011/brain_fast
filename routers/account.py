"""학습자 계정찾기 API. 화면은 학습자 Vue 프로젝트의 /find-account 가 맡는다. 기존 commonness 라우터와 분리한다."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from db import get_db
from schemas.accountSchemas import FindId, FindPw, VerifyAccount
from services import accountServices as service


class AccountRoute(APIRoute):
    """계정찾기의 오류 응답·로그에 입력한 비밀번호를 포함하지 않는다."""
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

