from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models.popupModel import PopupModel


def list_all(db: Session, *, active_only: bool = False) -> list[PopupModel]:
    stmt = select(PopupModel).where(PopupModel.del_yn == "N")
    if active_only:
        stmt = stmt.where(PopupModel.state == "N")
    stmt = stmt.order_by(PopupModel.pp_sort.asc(), PopupModel.idx.desc())
    return list(db.scalars(stmt).all())


def list_public_active(db: Session, *, now: datetime | None = None) -> list[PopupModel]:
    """학습자 메인용: 삭제/숨김 제외, 기간 안인 팝업."""
    now = now or datetime.now()
    stmt = (
        select(PopupModel)
        .where(
            PopupModel.del_yn == "N",
            PopupModel.state == "N",
            or_(PopupModel.start_at.is_(None), PopupModel.start_at <= now),
            or_(PopupModel.end_at.is_(None), PopupModel.end_at >= now),
        )
        .order_by(PopupModel.pp_sort.asc(), PopupModel.idx.desc())
    )
    return list(db.scalars(stmt).all())


def get_by_idx(db: Session, idx: int) -> PopupModel | None:
    return db.get(PopupModel, idx)


def create(
    db: Session,
    *,
    pp_title: str | None,
    pp_image: str,
    pp_link: str | None,
    pp_sort: int = 0,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    state: str = "N",
) -> PopupModel:
    row = PopupModel(
        del_yn="N",
        pp_title=pp_title,
        pp_image=pp_image,
        pp_link=pp_link,
        pp_sort=pp_sort,
        start_at=start_at,
        end_at=end_at,
        state=state,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update(
    db: Session,
    row: PopupModel,
    *,
    pp_title: str | None,
    pp_link: str | None,
    pp_sort: int,
    start_at: datetime | None,
    end_at: datetime | None,
    state: str,
    pp_image: str | None = None,
    updated_at: str | None = None,
) -> PopupModel:
    row.pp_title = pp_title
    row.pp_link = pp_link
    row.pp_sort = pp_sort
    row.start_at = start_at
    row.end_at = end_at
    row.state = state
    if pp_image is not None:
        row.pp_image = pp_image
    if updated_at is not None:
        row.updated_at = updated_at
    db.commit()
    db.refresh(row)
    return row


def soft_delete(db: Session, row: PopupModel) -> PopupModel:
    row.del_yn = "Y"
    row.state = "S"
    db.commit()
    db.refresh(row)
    return row
