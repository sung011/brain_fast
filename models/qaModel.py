from datetime import datetime
from typing import Optional

from sqlalchemy import CHAR, DateTime, Identity, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class QaThreadModel(Base):
    """Q&A 문의 스레드 (qa_thread)."""

    __tablename__ = "qa_thread"

    idx: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    del_yn: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")
    qt_u_idx: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    qt_title: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    qt_last_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    qt_last_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    qt_unread_admin: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    qt_unread_user: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    left_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    state: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")


class QaMessageModel(Base):
    """Q&A 메시지 (qa_message)."""

    __tablename__ = "qa_message"

    idx: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    del_yn: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")
    qm_t_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    qm_u_idx: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    qm_role: Mapped[str] = mapped_column(CHAR(1), nullable=False)
    qm_body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    left_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    state: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")
