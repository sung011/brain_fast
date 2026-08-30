from datetime import datetime
from typing import Optional

from sqlalchemy import CHAR, DateTime, Identity, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class UserModel(Base):
    __tablename__ = "user_member"

    idx: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    del_yn: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")
    user_id: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    user_pw: Mapped[str] = mapped_column(String(255), nullable=False)
    user_name: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    user_birth: Mapped[str] = mapped_column(String(10), nullable=False)
    user_un: Mapped[str] = mapped_column(String(20), nullable=False)
    user_sp: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    left_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    mb_level: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    state: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")
