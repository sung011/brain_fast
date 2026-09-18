from datetime import datetime
from typing import Optional

from sqlalchemy import CHAR, DateTime, Identity, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class ReviewNodeModel(Base):
    """학습 제출 리뷰 (review_node 테이블)."""

    __tablename__ = "review_node"

    idx: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    del_yn: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")
    rn_u_idx: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rn_s_idx: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rn_disease: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    rn_image: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rn_solution: Mapped[Optional[str]] = mapped_column(CHAR(1), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    left_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    state: Mapped[str] = mapped_column(CHAR(1), nullable=False, server_default="N")
