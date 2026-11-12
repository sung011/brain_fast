"""마이페이지 정보 수정(이름·학교·전공)과 비밀번호 변경. 합성/로컬 구현 범위 — FOLLOWUP2 (U2=B).

- 회원은 요청이 아니라 **서버 세션의 learner** 로만 정한다(GET /me 와 같은 인증: 양의 int·bool 금지·email_verified is True·활성 회원).
- 쓰기는 모두 **회원 행 잠금(FOR UPDATE) + 최신값 재조회** 안에서 한다: 충돌 확인(버전) → 현재 비밀번호 확인(지금 DB 해시 기준) → 쓰기 → commit. 실패는 rollback.
- 이름·학교·전공은 복구 기준이므로 수정에도 현재 비밀번호가 필요하다. 비밀번호는 bcrypt 해시(utils.password)만 쓴다.
- 시도 제한: 승인된 회원별 5회 + IP별 30회 / 600초(프로필·비밀번호 공통). **잘못된 비밀번호도 소모하고 성공해도 초기화하지 않는다.** 메모리 방식이라 재시작·다중 worker 에서는 공유되지 않는다(R04).
- 탈퇴·복구 변경·생년월일/이메일 변경은 이 모듈에 없다.
"""
import hashlib
import hmac

from sqlalchemy.orm import Session

from config import settings
from models.userModel import UserModel
from repositories import accountProfileRepositories as profile_repositories
from repositories import accountSettingsRepositories as repositories
from schemas.accountProfileSchemas import MeProfile
from schemas.accountSettingsSchemas import PasswordChange, ProfilePatch, SettingsResponse
from services.accountProfileServices import NotApproved, NotAuthenticated, _learner_idx
from services.userServices import format_updated_at
from utils.password import hash_password, verify_password
from utils.rate_limit import RateLimiter

WINDOW_SECONDS = 10 * 60
learner_limiter = RateLimiter(max_attempts=5, window_seconds=WINDOW_SECONDS)
ip_limiter = RateLimiter(max_attempts=30, window_seconds=WINDOW_SECONDS)


class TooManyAttempts(Exception):
    """시도 횟수 제한을 넘었다 → 429."""


class BadCurrentPassword(Exception):
    """현재 비밀번호가 맞지 않다 → 400(쓰기 0)."""


class PasswordMismatch(Exception):
    """새 비밀번호와 확인이 다르다 → 400."""


class SamePassword(Exception):
    """새 비밀번호가 현재 비밀번호와 같다 → 400."""


class ProfileConflict(Exception):
    """불러온 뒤 다른 곳에서 프로필이 바뀌었다(버전 불일치) → 409, 덮어쓰지 않는다."""


def profile_version(user: UserModel) -> str:
    """프로필의 현재 상태를 가리키는 **불투명한** 충돌 확인용 문자열. 세션 서명 비밀로 HMAC 해서 값 자체는 알아볼 수 없다.
    이름·학교·전공·수정일시가 바뀌면(관리자 변경 포함) 달라진다. 새 DB 칸은 쓰지 않는다."""
    parts = ["profile_v1", str(user.idx), user.user_name or "", user.user_un or "", user.user_sp or "", user.updated_at or ""]
    return hmac.new(settings.secret_key.encode("utf-8"), "\x1f".join(parts).encode("utf-8"), hashlib.sha256).hexdigest()


def _response(user: UserModel) -> SettingsResponse:
    return SettingsResponse(
        profile=MeProfile(idx=user.idx, user_id=user.user_id, user_name=user.user_name, user_un=user.user_un, user_sp=user.user_sp),
        profile_version=profile_version(user),
    )


def _authenticated(db: Session, session: object) -> UserModel:
    idx, approved = _learner_idx(session)
    user = profile_repositories.get_active_member(db, idx)
    if user is None:
        raise NotAuthenticated
    if not approved:
        raise NotApproved
    return user


def _check_limit(user_idx: int, client_ip: str) -> None:
    # 두 제한을 모두 기록해야 IP 를 바꿔 가며 한 계정을 노리는 경우도 막힌다.
    learner_ok = learner_limiter.hit(f"learner:{user_idx}")
    ip_ok = ip_limiter.hit(f"ip:{client_ip}")
    if not (learner_ok and ip_ok):
        raise TooManyAttempts


def get_settings(db: Session, session: object) -> SettingsResponse:
    return _response(_authenticated(db, session))


def update_profile(db: Session, session: object, body: ProfilePatch, client_ip: str) -> SettingsResponse:
    user = _authenticated(db, session)
    _check_limit(user.idx, client_ip)
    try:
        locked = repositories.lock_active_member(db, user.idx)
        if locked is None:
            raise NotAuthenticated
        if not hmac.compare_digest(body.expected_version, profile_version(locked)):
            raise ProfileConflict
        if not verify_password(body.current_password, locked.user_pw):
            raise BadCurrentPassword
        if (locked.user_name, locked.user_un, locked.user_sp) != (body.user_name, body.user_un, body.user_sp):
            repositories.apply_profile(db, locked, body.user_name, body.user_un, body.user_sp, format_updated_at())
        db.commit()
        db.refresh(locked)
        return _response(locked)
    except Exception:
        db.rollback()
        raise


def change_password(db: Session, session: object, body: PasswordChange, client_ip: str) -> None:
    """현재 비밀번호를 지금 DB 해시로 다시 확인한 뒤 새 해시를 저장한다. 성공 뒤 이 요청의 learner 세션 정리는 라우터가 한다."""
    user = _authenticated(db, session)
    _check_limit(user.idx, client_ip)
    if not hmac.compare_digest(body.new_password.encode("utf-8"), body.new_password_confirm.encode("utf-8")):
        raise PasswordMismatch
    try:
        locked = repositories.lock_active_member(db, user.idx)
        if locked is None:
            raise NotAuthenticated
        if not verify_password(body.current_password, locked.user_pw):
            raise BadCurrentPassword
        if hmac.compare_digest(body.new_password.encode("utf-8"), body.current_password.encode("utf-8")):
            raise SamePassword
        repositories.apply_password_hash(db, locked, hash_password(body.new_password), format_updated_at())
        db.commit()
    except Exception:
        db.rollback()
        raise
