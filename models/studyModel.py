from datetime import datetime
from typing import Optional

from sqlalchemy import CHAR, DateTime, Identity, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class StudyModel(Base):
    """학습 영상 메타데이터 (study 테이블)."""

    __tablename__ = "study"

    idx: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    del_yn: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")
    st_part: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    st_modal: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    st_image: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    st_disease: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    left_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    state: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")
