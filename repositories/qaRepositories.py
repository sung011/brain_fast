from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.qaModel import QaMessageModel, QaThreadModel
from models.userModel import UserModel


def list_threads(
    db: Session,
    *,
    user_idx: int | None = None,
    limit: int | None = None,
) -> list[tuple[QaThreadModel, UserModel | None]]:
    stmt = (
        select(QaThreadModel, UserModel)
        .outerjoin(UserModel, UserModel.idx == QaThreadModel.qt_u_idx)
        .where(QaThreadModel.del_yn == "N")
        .order_by(
            QaThreadModel.qt_last_at.desc().nullslast(),
            QaThreadModel.idx.desc(),
        )
    )
    if user_idx is not None:
        stmt = stmt.where(QaThreadModel.qt_u_idx == user_idx)
    if limit is not None and limit > 0:
        stmt = stmt.limit(limit)
    return list(db.execute(stmt).all())


def count_admin_unread(db: Session) -> int:
    stmt = (
        select(func.coalesce(func.sum(QaThreadModel.qt_unread_admin), 0))
        .where(QaThreadModel.del_yn == "N", QaThreadModel.state == "N")
    )
    return int(db.scalar(stmt) or 0)


def get_thread(db: Session, idx: int) -> QaThreadModel | None:
    return db.get(QaThreadModel, idx)


def create_thread(
    db: Session,
    *,
    qt_u_idx: int,
    qt_title: str,
    qt_last_msg: str | None = None,
    qt_last_at: datetime | None = None,
    qt_unread_admin: int = 1,
) -> QaThreadModel:
    row = QaThreadModel(
        del_yn="N",
        qt_u_idx=qt_u_idx,
        qt_title=qt_title,
        qt_last_msg=qt_last_msg,
        qt_last_at=qt_last_at or datetime.now(),
        qt_unread_admin=qt_unread_admin,
        qt_unread_user=0,
        state="N",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_thread_meta(
    db: Session,
    row: QaThreadModel,
    *,
    qt_last_msg: str | None = None,
    qt_last_at: datetime | None = None,
    qt_unread_admin: int | None = None,
    qt_unread_user: int | None = None,
    state: str | None = None,
    updated_at: str | None = None,
) -> QaThreadModel:
    if qt_last_msg is not None:
        row.qt_last_msg = qt_last_msg
    if qt_last_at is not None:
        row.qt_last_at = qt_last_at
    if qt_unread_admin is not None:
        row.qt_unread_admin = qt_unread_admin
    if qt_unread_user is not None:
        row.qt_unread_user = qt_unread_user
    if state is not None:
        row.state = state
    if updated_at is not None:
        row.updated_at = updated_at
    db.commit()
    db.refresh(row)
    return row


def list_messages(db: Session, thread_idx: int) -> list[QaMessageModel]:
    stmt = (
        select(QaMessageModel)
        .where(
            QaMessageModel.del_yn == "N",
            QaMessageModel.qm_t_idx == thread_idx,
        )
        .order_by(QaMessageModel.created_at.asc(), QaMessageModel.idx.asc())
    )
    return list(db.scalars(stmt).all())


def create_message(
    db: Session,
    *,
    qm_t_idx: int,
    qm_u_idx: int | None,
    qm_role: str,
    qm_body: str,
) -> QaMessageModel:
    row = QaMessageModel(
        del_yn="N",
        qm_t_idx=qm_t_idx,
        qm_u_idx=qm_u_idx,
        qm_role=qm_role,
        qm_body=qm_body,
        state="N",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
