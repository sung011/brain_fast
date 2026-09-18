from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from models.userModel import UserModel


def list_solved_study_idxs(db: Session, user_idx: int) -> list[int]:
    """해당 사용자가 review_node에 제출한 study idx 목록."""
    stmt = (
        select(ReviewNodeModel.rn_s_idx)
        .where(
            ReviewNodeModel.del_yn == "N",
            ReviewNodeModel.rn_u_idx == user_idx,
            ReviewNodeModel.rn_s_idx.is_not(None),
        )
        .distinct()
    )
    return [int(idx) for idx in db.scalars(stmt).all() if idx is not None]


def list_with_details(
    db: Session,
    *,
    user_idx: int | None = None,
    limit: int | None = None,
) -> list[tuple[ReviewNodeModel, StudyModel | None, UserModel | None]]:
    """풀이 이력 + study·회원 조인. 최신순."""
    stmt = (
        select(ReviewNodeModel, StudyModel, UserModel)
        .outerjoin(StudyModel, StudyModel.idx == ReviewNodeModel.rn_s_idx)
        .outerjoin(UserModel, UserModel.idx == ReviewNodeModel.rn_u_idx)
        .where(ReviewNodeModel.del_yn == "N")
        .order_by(ReviewNodeModel.created_at.desc())
    )
    if user_idx is not None:
        stmt = stmt.where(ReviewNodeModel.rn_u_idx == user_idx)
    if limit is not None and limit > 0:
        stmt = stmt.limit(limit)
    return list(db.execute(stmt).all())


def count_by_user(db: Session, user_idx: int) -> int:
    """해당 사용자의 풀이(제출) 건수."""
    stmt = (
        select(func.count())
        .select_from(ReviewNodeModel)
        .where(
            ReviewNodeModel.del_yn == "N",
            ReviewNodeModel.rn_u_idx == user_idx,
        )
    )
    return int(db.scalar(stmt) or 0)


def create(
    db: Session,
    *,
    rn_u_idx: int | None,
    rn_s_idx: int | None,
    rn_disease: str | None,
    rn_image: str | None,
    rn_solution: str | None,
    state: str = "N",
) -> ReviewNodeModel:
    row = ReviewNodeModel(
        del_yn="N",
        rn_u_idx=rn_u_idx,
        rn_s_idx=rn_s_idx,
        rn_disease=rn_disease,
        rn_image=rn_image,
        rn_solution=rn_solution,
        state=state,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
