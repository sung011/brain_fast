"""가입 정보 확인과 계정찾기. 메일 발송·테이블 생성은 하지 않는다.

비밀번호 찾기(복구 B안): 시도 제한(identity 5 / IP 30, 성공도 소모) → 회원·답 조회 → 세 슬롯 bcrypt 검증(단락 없음, 더미로 3회 맞춤).
reset 은 verify 이력을 믿지 않고 신원·답을 다시 검증한다: 회원 행 잠금(FOR UPDATE) → 답 3행 최신 조회 → 재검증 → 새 해시 저장 → commit.
유효한 reset 두 건은 잠금 순서대로 둘 다 성공할 수 있다(일회용 토큰 없음). 답이 바뀐 뒤의 옛 답은 거절된다.
"""
from hmac import compare_digest

from sqlalchemy.orm import Session

from repositories import accountRecoveryRepositories as recovery_repositories
from repositories import accountRepositories as repositories
from schemas.accountRecoverySchemas import answer_map
from schemas.accountSchemas import FindId, FindPw, VerifyAccount
from services import accountRecoveryServices as recovery
from services.userServices import format_updated_at, resolve_account_id
from utils.password import hash_password
from utils.rate_limit import RateLimiter

# 시도 횟수 제한. 아이디 찾기·가입 정보 확인·비밀번호 변경을 합쳐서 센다.
# IP는 같은 망의 여러 사용자를 고려해 넉넉하게, 계정(아이디 또는 이름+생년월일)은 좁게 둔다.
WINDOW_SECONDS = 10 * 60
ip_limiter = RateLimiter(max_attempts=30, window_seconds=WINDOW_SECONDS)
identity_limiter = RateLimiter(max_attempts=5, window_seconds=WINDOW_SECONDS)


class TooManyAttempts(Exception):
    """시도 횟수 제한을 넘었다."""


class PasswordMismatch(ValueError):
    """새 비밀번호와 확인 비밀번호가 다르다 → 400."""


def _check_limit(client_ip: str, identity_key: str) -> None:
    # 두 제한을 모두 기록해야 IP를 바꿔 가며 한 계정을 노리는 경우도 막힌다.
    ip_ok = ip_limiter.hit(f"ip:{client_ip}")
    identity_ok = identity_limiter.hit(identity_key)
    if not (ip_ok and identity_ok):
        raise TooManyAttempts


def mask_user_id(user_id: str) -> str:
    """아이디(이메일)를 앞 두 글자만 남기고 가린다. 예: learner@example.com → le***@example.com"""
    local, at, domain = user_id.partition("@")
    keep = 2 if len(local) > 2 else 1
    return f"{local[:keep]}***{at}{domain}"


def find_id(db: Session, body: FindId, client_ip: str) -> list[str]:
    _check_limit(client_ip, f"birth:{body.user_name}:{body.user_birth}")
    return [mask_user_id(user_id) for user_id in repositories.find_ids(db, body.user_name, body.user_birth)]


def verify_account(db: Session, body: VerifyAccount, client_ip: str) -> bool:
    _check_limit(client_ip, f"id:{body.user_id.lower()}")   # bcrypt 보다 먼저
    user = repositories.find_member(db, resolve_account_id(db, body.user_id), body.user_name, body.user_birth)
    rows = recovery_repositories.answers_for(db, user.idx) if user is not None else {}
    matched = recovery.check_answers(rows, answer_map(body.answers))   # 회원이 없어도 3회 검증
    return user is not None and matched


def reset_password(db: Session, body: FindPw, client_ip: str) -> bool:
    _check_limit(client_ip, f"id:{body.user_id.lower()}")
    if not compare_digest(body.user_pw.encode("utf-8"), body.user_pw_confirm.encode("utf-8")):
        raise PasswordMismatch("새 비밀번호와 확인 비밀번호가 일치하지 않습니다.")
    # 새 비밀번호 해시는 잠금 전에 만든다(잠금 시간을 줄이고, 성공·실패 경로의 bcrypt 수를 같게 한다).
    new_hash = hash_password(body.user_pw)
    answers = answer_map(body.answers)
    # 화면의 확인 결과를 신뢰하지 않고, 변경 요청에서도 신원과 답을 잠금 안에서 다시 검사한다.
    try:
        user = repositories.lock_member_for_reset(db, resolve_account_id(db, body.user_id), body.user_name, body.user_birth)
        rows = recovery_repositories.answers_for(db, user.idx) if user is not None else {}
        matched = recovery.check_answers(rows, answers)
        if user is None or not matched:
            db.rollback()
            return False
        repositories.set_password(db, user, new_hash, format_updated_at())
        db.commit()
    except Exception:
        db.rollback()
        raise
    return True
