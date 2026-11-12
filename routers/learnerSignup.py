"""학습자 가입 POST /signup (복구 B안: 비밀번호 찾기 질문 답 3개를 회원과 한 트랜잭션으로 등록). routers/user.py 가 include 한다.

- 요청: 기존 6필드(user_id·user_pw·user_name·user_birth·user_un·user_sp) + recovery_answers(Q01·Q02·Q03 각 1개). 알 수 없는 필드는 422.
- 성공 응답 키는 기존과 같다: ok·idx·user_id·user_name. 비밀번호·해시·답은 넣지 않는다.
- 이미 있는 아이디(삭제 회원 포함) → 409 {"ok": false, "error_type": "http_error", "detail": {"message": ...}} — 기존 공용 처리기 응답과 같은 모양.
- 오류 응답·로그는 SignupSafeRoute(routers/accountSafeErrors.py): 허용 위치·고정 코드만, 고정 500, 모든 상태 no-store, 유지보수면 503.
- 가입에는 새 시도 제한을 두지 않는다(기존에 없던 정책, 계획 범위 밖).
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from db import get_db
from routers.accountSafeErrors import SignupSafeRoute
from schemas.userSchemas import LearnerSignup
from services import accountRecoveryServices as recovery_service

router = APIRouter(tags=["user"], route_class=SignupSafeRoute)
DUPLICATE_ID = "이미 사용 중인 아이디입니다."


@router.post("/signup")
def learner_signup(body: LearnerSignup, db: Session = Depends(get_db)):
    try:
        user = recovery_service.create_learner_with_answers(db, body)
    except recovery_service.DuplicateUserId:
        return JSONResponse(status_code=409, content={"ok": False, "error_type": "http_error", "detail": {"message": DUPLICATE_ID}})
    return {"ok": True, "idx": user.idx, "user_id": user.user_id, "user_name": user.user_name}
