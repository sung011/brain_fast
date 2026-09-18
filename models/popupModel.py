from datetime import datetime
from typing import Optional

from sqlalchemy import CHAR, DateTime, Identity, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class PopupModel(Base):
    """학습자 메인 홍보 팝업 (popup 테이블)."""

    __tablename__ = "popup"

    idx: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    del_yn: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")
    pp_title: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pp_image: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pp_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pp_sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    left_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    state: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")
