"""메일 인증번호의 발급 및 확인을 직렬화하는 DB 처리."""
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from models.signupEmailCodeModel import SignupEmailCodeModel as Code


def issue(db: Session, email_key: str, digest: str, now: datetime, ttl: timedelta, cooldown: timedelta) -> bool:
    db.execute(delete(Code).where(Code.expires_at <= now))
    values = {"code_digest": digest, "sent_at": now, "expires_at": now + ttl, "failed_attempts": 0}
    stmt = (
        insert(Code)
        .values(email_key=email_key, **values)
        .on_conflict_do_update(
            index_elements=[Code.email_key],
            set_=values,
            where=Code.sent_at <= now - cooldown,
        )
        .returning(Code.email_key)
    )
    return db.scalar(stmt) is not None


def discard_issue(db: Session, email_key: str, digest: str, sent_at: datetime) -> None:
    db.execute(delete(Code).where(
        Code.email_key == email_key,
        Code.code_digest == digest,
        Code.sent_at == sent_at,
    ))


def lock_code(db: Session, email_key: str) -> Code | None:
    stmt = select(Code).where(Code.email_key == email_key).with_for_update().execution_options(populate_existing=True)
    return db.scalar(stmt)

