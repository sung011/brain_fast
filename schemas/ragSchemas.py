"""의학용어 검색 API 응답. 칸 이름은 GET /glossary 항목과 같다."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RagGlossaryItem(BaseModel):
    id: int | None = None
    term_id: str | None = None
    name_ko: str
    name_en: str | None = None
    definition_ko: str | None = None
    definition_en: str | None = Field(None, description="영어 설명. 색인 definition")
    body_part: str | None = None
    tags: str | None = None
    tags_ko: str | None = None
    modality: str | None = None
    category: str | None = Field(None, description="term 용어, disease 관련 병")


class RagGlossaryOut(BaseModel):
    ok: bool = True
    q: str
    terms: list[str]
    count: int
    items: list[RagGlossaryItem]
