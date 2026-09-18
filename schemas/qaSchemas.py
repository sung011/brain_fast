"""Q&A API 스키마."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class QaCreateBody(BaseModel):
    user_idx: int = Field(..., description="문의 회원 idx")
    title: str = Field(..., description="문의 제목")
    body: str = Field(..., description="문의 내용")


class QaMessageBody(BaseModel):
    body: str = Field(..., description="메시지 내용")
    user_idx: int | None = Field(None, description="회원 쪽 전송 시 본인 idx")


class QaMessageOut(BaseModel):
    idx: int
    qm_t_idx: int
    qm_u_idx: int | None = None
    qm_role: str
    qm_body: str
    created_at: datetime | None = None


class QaThreadOut(BaseModel):
    idx: int
    qt_u_idx: int | None = None
    user_id: str | None = None
    user_name: str | None = None
    qt_title: str | None = None
    qt_last_msg: str | None = None
    qt_last_at: datetime | None = None
    qt_unread_admin: int = 0
    qt_unread_user: int = 0
    created_at: datetime | None = None
    state: str = "N"
