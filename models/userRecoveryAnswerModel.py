"""비밀번호 찾기 질문 답(복구 B안). 정규화한 답의 bcrypt 해시만 저장한다(평문 0).

테이블 정의는 docker/postgres/user_recovery_answer.sql(수동 적용 파일) 과 같아야 한다. 앱은 create_all 을 하지 않는다.
FK ON DELETE RESTRICT 는 잠정안이다(팀 합의 T2).
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from db import Base
from models.userModel import UserModel  # noqa: F401 — FK 대상 테이블을 같은 metadata 에 올린다


class UserRecoveryAnswerModel(Base):
    __tablename__ = "user_recovery_answer"
    __table_args__ = (
        CheckConstraint("ura_question_id IN ('Q01','Q02','Q03')", name="user_recovery_answer_qid_check"),
        UniqueConstraint("ura_u_idx", "ura_question_id", name="user_recovery_answer_user_question_uq"),
        {"comment": "비밀번호 찾기 질문 답변(정규화 후 bcrypt 해시만, 평문 없음)"},
    )

    idx: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    ura_u_idx: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user_member.idx", name="user_recovery_answer_user_fk", ondelete="RESTRICT"),
        nullable=False,
    )
    ura_question_id: Mapped[str] = mapped_column(String(8), nullable=False)
    ura_answer_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    ura_question_set: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'security_questions_v1'"))
    ura_norm_version: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'answer_norm_v1'"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    def __repr__(self) -> str:  # 해시를 repr 에 싣지 않는다
        return f"UserRecoveryAnswerModel(idx={self.idx!r}, ura_u_idx={self.ura_u_idx!r}, ura_question_id={self.ura_question_id!r})"
