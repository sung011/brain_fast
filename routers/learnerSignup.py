"""학습자 가입 POST /signup (복구 B안: 비밀번호 찾기 답을 회원과 한 트랜잭션으로 등록). routers/user.py 가 include 한다.

- 요청: 기존 6필드(user_id·user_pw·user_name·user_birth·user_un·user_sp) + recovery_answers(새 이메일 가입은 선택 질문 1개). 알 수 없는 필드는 422.
- 성공 응답 키는 기존과 같다: ok·idx·user_id·user_name. 비밀번호·해시·답은 넣지 않는다.
- 이미 있는 아이디(삭제 회원 포함) → 409 {"ok": false, "error_type": "http_error", "detail": {"message": ...}} — 기존 공용 처리기 응답과 같은 모양.
- 오류 응답·로그는 SignupSafeRoute(routers/accountSafeErrors.py): 허용 위치·고정 코드만, 고정 500, 모든 상태 no-store, 유지보수면 503.
- 가입에는 새 시도 제한을 두지 않는다(기존에 없던 정책, 계획 범위 밖).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from db import get_db
from routers.accountSafeErrors import SignupSafeRoute
from schemas.signupEmailSchemas import SignupEmailCode, SignupEmailVerify
from schemas.userSchemas import LearnerSignup
from services import accountRecoveryServices as recovery_service
from services import signupEmailServices as signup_email

router = APIRouter(tags=["user"], route_class=SignupSafeRoute)
DUPLICATE_ID = "이미 사용 중인 아이디입니다."


MAIL_LIMIT = "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
MAIL_UNAVAILABLE = "이메일 인증을 사용할 수 없습니다. 잠시 후 다시 시도해 주세요."


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/signup/email-code", status_code=202)
def send_signup_email_code(body: SignupEmailCode, request: Request, db: Session = Depends(get_db)):
    try:
        signup_email.send_code(db, body.email, _client_ip(request))
    except signup_email.EmailCodeThrottled:
        raise HTTPException(429, MAIL_LIMIT) from None
    except signup_email.EmailSignupUnavailable:
        raise HTTPException(503, MAIL_UNAVAILABLE) from None
    return {"ok": True}


@router.post("/signup/email-code/verify")
def verify_signup_email_code(body: SignupEmailVerify, request: Request, db: Session = Depends(get_db)):
    try:
        signup_email.verify_code(db, body.email, body.email_code, _client_ip(request))
    except signup_email.EmailCodeRequired:
        raise HTTPException(400, "인증번호를 확인해 주세요. 만료되었다면 다시 받아 주세요.") from None
    except signup_email.EmailCodeThrottled:
        raise HTTPException(429, MAIL_LIMIT) from None
    except signup_email.EmailSignupUnavailable:
        raise HTTPException(503, MAIL_UNAVAILABLE) from None
    return {"ok": True}


@router.post("/signup")
def learner_signup(body: LearnerSignup, request: Request, db: Session = Depends(get_db)):
    try:
        user = recovery_service.create_learner_with_answers(db, body, _client_ip(request))
    except signup_email.EmailCodeRequired:
        raise HTTPException(400, "이메일과 인증번호를 확인해 주세요. 인증번호가 만료되었다면 다시 받아 주세요.") from None
    except signup_email.EmailCodeThrottled:
        raise HTTPException(429, MAIL_LIMIT) from None
    except signup_email.EmailSignupUnavailable:
        raise HTTPException(503, MAIL_UNAVAILABLE) from None
    except recovery_service.DuplicateUserId:
        return JSONResponse(status_code=409, content={"ok": False, "error_type": "http_error", "detail": {"message": DUPLICATE_ID}})
    return {"ok": True, "idx": user.idx, "user_id": user.user_id, "user_name": user.user_name}
