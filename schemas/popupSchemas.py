"""홍보 팝업 API 스키마."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PopupOut(BaseModel):
    idx: int
    del_yn: str = "N"
    pp_title: str | None = None
    pp_image: str | None = None
    pp_image_url: str | None = None
    pp_link: str | None = None
    pp_sort: int = 0
    start_at: datetime | None = None
    end_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: str | None = None
    state: str = "N"

    model_config = ConfigDict(from_attributes=True)


class PopupPublicOut(BaseModel):
    idx: int
    pp_title: str | None = None
    pp_image: str | None = None
    pp_image_url: str | None = None
    pp_link: str | None = None
    pp_sort: int = 0


class PopupWriteResult(BaseModel):
    ok: bool = True
    idx: int
    pp_title: str | None = None
    pp_image: str | None = None
    pp_link: str | None = None
    pp_sort: int = 0
    state: str = "N"
    remote_path: str | None = None
