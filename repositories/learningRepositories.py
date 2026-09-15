from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models.medicalTermsModel import MedicalTermsModel


def get_by_body_part(
        db: Session,
        region: str,
        *,
        modality: str | None = None,
) -> list[MedicalTermsModel]:
    """부위(body_part)로 의학용어 목록을 조회한다.

    modality가 있으면 해당 값과 common을 함께 조회한다.
    (현재 데이터는 대부분 modality='common')
    """
    stmt = select(MedicalTermsModel).where(MedicalTermsModel.body_part == region)
    if modality is not None:
        stmt = stmt.where(
            or_(
                MedicalTermsModel.modality == modality,
                MedicalTermsModel.modality == "common",
            )
        )
    stmt = stmt.order_by(MedicalTermsModel.name_ko.asc())
    return list(db.scalars(stmt).all())
