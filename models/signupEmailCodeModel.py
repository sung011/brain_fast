"""가입 인증번호의 검증용 지문과 유효기간."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class SignupEmailCodeModel(Base):
    __tablename__ = "signup_email_code"
    __table_args__ = {"schema": "public"}

    email_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False)

