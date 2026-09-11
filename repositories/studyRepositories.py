from sqlalchemy import select
from sqlalchemy.orm import Session

from models.studyModel import StudyModel


def list_all(db: Session, *, active_only: bool = True) -> list[StudyModel]:
    stmt = select(StudyModel).order_by(StudyModel.created_at.desc())
    if active_only:
        stmt = stmt.where(
            StudyModel.del_yn == "N",
            StudyModel.state == "N",
        )
    return list(db.scalars(stmt).all())


def get_by_idx(db: Session, idx: int) -> StudyModel | None:
    return db.get(StudyModel, idx)


def create(
    db: Session,
    *,
    st_part: str,
    st_modal: str,
    st_disease: str,
    st_image: str,
) -> StudyModel:
    row = StudyModel(
        del_yn="N",
        st_part=st_part,
        st_modal=st_modal,
        st_disease=st_disease,
        st_image=st_image,
        state="N",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update(
    db: Session,
    row: StudyModel,
    *,
    st_part: str,
    st_modal: str,
    st_disease: str,
    st_image: str | None = None,
    updated_at: str | None = None,
) -> StudyModel:
    row.st_part = st_part
    row.st_modal = st_modal
    row.st_disease = st_disease
    if st_image is not None:
        row.st_image = st_image
    if updated_at is not None:
        row.updated_at = updated_at
    db.commit()
    db.refresh(row)
    return row


def soft_delete(db: Session, row: StudyModel) -> StudyModel:
    row.del_yn = "Y"
    row.state = "S"
    db.commit()
    db.refresh(row)
    return row
