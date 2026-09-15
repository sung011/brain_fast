from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Identity, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class MedicalTermsModel(Base):
    """의학용어 사전 (medical_terms 테이블)."""

    __tablename__ = "medical_terms"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    term_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name_ko: Mapped[str] = mapped_column(Text, nullable=False)
    name_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    definition_ko: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body_part: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'::text"))
    tags_ko: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'{}'::text"))
    modality: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'common'::text"))
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    snomed_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    concept_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    snomed_fsn: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mapping_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    classify_method: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'HINS_KOSTOM'::text"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
