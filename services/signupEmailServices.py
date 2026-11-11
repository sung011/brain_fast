"""가입 전에 받은 편지함의 소유를 일회용 코드로 확인한다."""
import hashlib
import hmac
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy.orm import Session

from config import settings
from repositories import signupEmailCodeRepositories as codes
from schemas.signupEmailSchemas import normalize_email
from utils.rate_limit import RateLimiter

CODE_TTL = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(minutes=1)
MAX_FAILURES = 5
send_ip_limit = RateLimiter(10, 600)
send_total_limit = RateLimiter(60, 600)
verify_ip_limit = RateLimiter(30, 600)


class EmailCodeRequired(ValueError):
    """이메일이나 유효한 인증번호를 다시 입력해야 한다."""


class EmailCodeThrottled(Exception):
    """메일 재발송 또는 확인 제한."""


class EmailSignupUnavailable(Exception):
    """메일 가입 기능이 꺼져 있거나 발송 환경이 준비되지 않았다."""


def _key() -> bytes:
    key = settings.signup_email_code_secret.get_secret_value().encode("utf-8")
    if len(key) < 32:
        raise EmailSignupUnavailable
    return key


def _digest(key: bytes, purpose: str, email: str, code: str = "") -> str:
    payload = f"{purpose}\0{email}\0{code}".encode("ascii")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _send(email: str, code: str) -> None:
    if not settings.signup_smtp_host or not settings.signup_smtp_from:
        raise EmailSignupUnavailable
    message = EmailMessage()
    message["From"] = settings.signup_smtp_from
    message["To"] = email
    message["Subject"] = "메디스캔노트 가입 인증번호"
    message.set_content(f"가입 화면에 인증번호 {code}를 입력해 주세요. 10분 동안 사용할 수 있습니다.")
    context = ssl.create_default_context()
    try:
        if settings.signup_smtp_ssl:
            client = smtplib.SMTP_SSL(settings.signup_smtp_host, settings.signup_smtp_port, timeout=8, context=context)
        else:
            client = smtplib.SMTP(settings.signup_smtp_host, settings.signup_smtp_port, timeout=8)
        with client:
            if not settings.signup_smtp_ssl:
                client.starttls(context=context)
            if settings.signup_smtp_user:
                client.login(settings.signup_smtp_user, settings.signup_smtp_password.get_secret_value())
            client.send_message(message, to_addrs=[email])
    except (OSError, smtplib.SMTPException, ValueError):
        raise EmailSignupUnavailable from None


def send_code(db: Session, email: str, ip: str) -> None:
    if not settings.signup_email_verification_enabled:
        raise EmailSignupUnavailable
    key = _key()
    if not send_ip_limit.hit(ip) or not send_total_limit.hit("global"):
        raise EmailCodeThrottled
    code = f"{secrets.randbelow(100_000_000):08d}"
    now = datetime.now(timezone.utc)
    email_key = _digest(key, "email", email)
    digest = _digest(key, "code", email, code)
    if not codes.issue(db, email_key, digest, now, CODE_TTL, RESEND_COOLDOWN):
        db.rollback()
        raise EmailCodeThrottled
    db.commit()
    # SMTP 호출 전에 커밋해 잠금을 풀고, 실패한 발급만 별도 트랜잭션으로 무효화한다.
    try:
        _send(email, code)
    except Exception:
        try:
            codes.discard_issue(db, email_key, digest, now)
            db.commit()
        except Exception:
            db.rollback()
            raise
        raise


def _checked_code(db: Session, user_id: str, code: str | None, ip: str):
    """두 확인 입구가 동일한 잠금·오답·유효기간 검사를 사용한다."""
    if not verify_ip_limit.hit(ip):
        raise EmailCodeThrottled
    if not code:
        raise EmailCodeRequired
    try:
        email = normalize_email(user_id)
    except ValueError:
        raise EmailCodeRequired from None
    key = _key()
    row = codes.lock_code(db, _digest(key, "email", email))
    if row is None or row.expires_at <= datetime.now(timezone.utc) or row.failed_attempts >= MAX_FAILURES:
        raise EmailCodeRequired
    if not hmac.compare_digest(row.code_digest, _digest(key, "code", email, code)):
        row.failed_attempts += 1
        db.commit()
        raise EmailCodeRequired
    return email, row


def verify_code(db: Session, email: str, code: str, ip: str) -> None:
    """화면 확인은 번호를 소비하지 않고, 성공·실패 모두 잠금을 해제한다."""
    if not settings.signup_email_verification_enabled:
        raise EmailSignupUnavailable
    try:
        _checked_code(db, email, code, ip)
    finally:
        db.rollback()


def require_code(db: Session, user_id: str, code: str | None, ip: str) -> str:
    """가입 시에도 재검증하고 회원·답 저장과 같은 트랜잭션에서 소비한다."""
    email, row = _checked_code(db, user_id, code, ip)
    db.delete(row)
    return email

