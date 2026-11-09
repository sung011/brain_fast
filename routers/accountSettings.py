"""마이페이지 정보 수정·비밀번호 변경 API (routers/user.py 가 include 한다 — main.py 는 수정하지 않는다).

- GET   /me/settings : 승인된 본인의 프로필 5필드 + 충돌 확인용 버전(불투명).
- PATCH /me/profile  : 이름·학교·전공 + 현재 비밀번호 + expected_version. 충돌이면 409(덮어쓰지 않음).
- POST  /me/password : 현재 비밀번호 확인 → 새 비밀번호 저장 → **이 요청의 learner 세션만** 종료(관리자·게스트·다른 기기는 건드리지 않는다).
- GET   /me/recovery-questions : 비밀번호 찾기 질문 답 등록 여부만(답·해시 0).
- PUT   /me/recovery-questions : 현재 비밀번호 + 답 3개로 최초 등록·변경(복구 B안). 답이 들어가는 주소라 AnswerSafeRoute 를 쓴다.

쓰기(PATCH/POST)는 로그아웃과 같은 **같은 출처(Origin) 검사**를 먼저 한다. 회원·번호·아이디 쿼리는 400. 모든 응답 no-store.
오류 응답·로그에는 입력한 비밀번호·이름·학교·전공을 담지 않는다(AccountRoute: 검증 오류는 위치·종류만, 예상 못한 오류는 고정 문구 500).
"""
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from db import get_db
from routers.account import AccountRoute, _same_origin_request, client_ip
from routers.accountSafeErrors import AnswerSafeRoute
from schemas.accountRecoverySchemas import RecoveryQuestionsPut
from schemas.accountSettingsSchemas import PasswordChange, PasswordChangedResponse, ProfilePatch
from services import accountRecoveryServices as recovery_service
from services import accountSettingsServices as service

router = APIRouter(tags=["account-settings"], route_class=AccountRoute)
recovery_router = APIRouter(tags=["account-settings"], route_class=AnswerSafeRoute)

_IDENTITY_QUERY_KEYS = {"user_idx", "idx", "user_id", "uid", "id"}
UNAUTHORIZED = "로그인이 필요합니다."
NOT_APPROVED = "관리자 승인 후 사용할 수 있습니다."
FORBIDDEN_ORIGIN = "허용되지 않은 요청입니다."
TOO_MANY = "시도 횟수가 너무 많습니다. 10분 후 다시 시도해 주세요."
BAD_PASSWORD = "현재 비밀번호가 맞지 않습니다."
CONFLICT = "다른 곳에서 정보가 바뀌었습니다. 다시 불러온 뒤 확인해 주세요."


def _check_query(request: Request) -> None:
    if any(key.lower() in _IDENTITY_QUERY_KEYS for key in request.query_params.keys()):
        raise HTTPException(400, "회원은 로그인 정보로만 확인합니다.")


def _strict_same_origin(request: Request) -> bool:
    """로그아웃과 같은 출처 검사에 더해, Origin 값에 경로·쿼리·조각·사용자 정보가 붙어 있으면 거절한다(브라우저의 진짜 Origin 에는 없다)."""
    if not _same_origin_request(request):
        return False
    parts = urlsplit(request.headers.get("origin", ""))
    return parts.path == "" and not parts.query and not parts.fragment and "@" not in parts.netloc


def _check_write(request: Request) -> None:
    if not _strict_same_origin(request):
        raise HTTPException(403, FORBIDDEN_ORIGIN)
    _check_query(request)


def _session(request: Request):
    return request.scope.get("session")


def _translate(exc: Exception):
    """서비스 오류 → HTTP 오류. 알 수 없는 오류는 그대로 올려 AccountRoute 가 고정 500 으로 닫는다."""
    if isinstance(exc, service.NotAuthenticated):
        return HTTPException(401, UNAUTHORIZED)
    if isinstance(exc, service.NotApproved):
        return HTTPException(403, NOT_APPROVED)
    if isinstance(exc, service.TooManyAttempts):
        return HTTPException(429, TOO_MANY)
    if isinstance(exc, service.BadCurrentPassword):
        return HTTPException(400, BAD_PASSWORD)
    if isinstance(exc, service.PasswordMismatch):
        return HTTPException(400, "새 비밀번호와 확인 비밀번호가 일치하지 않습니다.")
    if isinstance(exc, service.SamePassword):
        return HTTPException(400, "새 비밀번호는 현재 비밀번호와 달라야 합니다.")
    if isinstance(exc, service.ProfileConflict):
        return HTTPException(409, CONFLICT)
    return None


_HANDLED = (service.NotAuthenticated, service.NotApproved, service.TooManyAttempts, service.BadCurrentPassword,
            service.PasswordMismatch, service.SamePassword, service.ProfileConflict)


@router.get("/me/settings")
def get_settings(request: Request, db: Session = Depends(get_db)):
    _check_query(request)
    try:
        result = service.get_settings(db, _session(request))
    except _HANDLED as exc:
        raise _translate(exc) from exc
    return result.model_dump(mode="json")


@router.patch("/me/profile")
def patch_profile(body: ProfilePatch, request: Request, db: Session = Depends(get_db)):
    _check_write(request)
    try:
        result = service.update_profile(db, _session(request), body, client_ip(request))
    except _HANDLED as exc:
        raise _translate(exc) from exc
    return result.model_dump(mode="json")


@router.post("/me/password")
def post_password(body: PasswordChange, request: Request, db: Session = Depends(get_db)):
    _check_write(request)
    try:
        service.change_password(db, _session(request), body, client_ip(request))
    except _HANDLED as exc:
        raise _translate(exc) from exc
    session = _session(request)
    if isinstance(session, dict):
        session.pop("learner", None)   # commit 이 끝난 뒤, 이 요청의 학습자 세션 키만 제거(관리자 세션·게스트 쿠키·다른 기기는 그대로)
    return PasswordChangedResponse().model_dump(mode="json")


@recovery_router.get("/me/recovery-questions")
def get_recovery_questions(request: Request, db: Session = Depends(get_db)):
    _check_query(request)
    try:
        result = recovery_service.get_status(db, _session(request))
    except _HANDLED as exc:
        raise _translate(exc) from exc
    return result.model_dump(mode="json")


@recovery_router.put("/me/recovery-questions")
def put_recovery_questions(body: RecoveryQuestionsPut, request: Request, db: Session = Depends(get_db)):
    _check_write(request)
    try:
        result = recovery_service.save_answers(db, _session(request), body, client_ip(request))
    except _HANDLED as exc:
        raise _translate(exc) from exc
    return result.model_dump(mode="json")


router.include_router(recovery_router)
