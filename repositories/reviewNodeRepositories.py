from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from models.userModel import UserModel


def get_owned(
    db: Session, idx: int, user_idx: int
) -> tuple[ReviewNodeModel, StudyModel | None] | None:
    """그 사용자의 제출 1건과 연결된 study."""
    stmt = (
        select(ReviewNodeModel, StudyModel)
        .outerjoin(StudyModel, StudyModel.idx == ReviewNodeModel.rn_s_idx)
        .where(
            ReviewNodeModel.idx == idx,
            ReviewNodeModel.del_yn == "N",
            ReviewNodeModel.rn_u_idx == user_idx,
        )
    )
    row = db.execute(stmt).first()
    if row is None:
        return None
    return row[0], row[1]


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


def latest_solutions(db: Session, user_idx: int, study_idxs: list[int]) -> dict[int, ReviewNodeModel]:
    """study 마다 그 사용자의 가장 최근 풀이 1건."""
    if not study_idxs:
        return {}
    stmt = (
        select(ReviewNodeModel)
        .where(
            ReviewNodeModel.del_yn == "N",
            ReviewNodeModel.rn_u_idx == user_idx,
            ReviewNodeModel.rn_s_idx.in_(study_idxs),
        )
        .order_by(ReviewNodeModel.created_at.desc(), ReviewNodeModel.idx.desc())
    )
    latest: dict[int, ReviewNodeModel] = {}
    for row in db.scalars(stmt).all():
        latest.setdefault(int(row.rn_s_idx), row)
    return latest


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


def list_for_user(
    db: Session,
    user_idx: int,
    *,
    solutions: list[str] | None = None,
    limit: int | None = None,
) -> list[tuple[ReviewNodeModel, StudyModel | None]]:
    """학습자 본인의 풀이 기록 + 문제. 최신순. solutions 가 있으면 그 결과 코드(C/H/W)만."""
    stmt = (
        select(ReviewNodeModel, StudyModel)
        .outerjoin(StudyModel, StudyModel.idx == ReviewNodeModel.rn_s_idx)
        .where(
            ReviewNodeModel.del_yn == "N",
            ReviewNodeModel.rn_u_idx == user_idx,
        )
        .order_by(ReviewNodeModel.created_at.desc(), ReviewNodeModel.idx.desc())
    )
    if solutions:
        stmt = stmt.where(ReviewNodeModel.rn_solution.in_(solutions))
    if limit is not None and limit > 0:
        stmt = stmt.limit(limit)
    return list(db.execute(stmt).all())


def get_for_user(
    db: Session,
    review_idx: int,
    user_idx: int,
) -> tuple[ReviewNodeModel, StudyModel | None] | None:
    """학습자 본인의 풀이 기록 1건. 다른 사람 기록이면 None."""
    stmt = (
        select(ReviewNodeModel, StudyModel)
        .outerjoin(StudyModel, StudyModel.idx == ReviewNodeModel.rn_s_idx)
        .where(
            ReviewNodeModel.idx == review_idx,
            ReviewNodeModel.del_yn == "N",
            ReviewNodeModel.rn_u_idx == user_idx,
        )
    )
    row = db.execute(stmt).first()
    return (row[0], row[1]) if row else None


def list_between_for_user(
    db: Session,
    user_idx: int,
    *,
    start: datetime,
    end: datetime,
) -> list[tuple[ReviewNodeModel, StudyModel | None]]:
    """한 회원의 기간 안 제출과 연결된 study. start 이상, end 미만."""
    stmt = (
        select(ReviewNodeModel, StudyModel)
        .outerjoin(StudyModel, StudyModel.idx == ReviewNodeModel.rn_s_idx)
        .where(
            ReviewNodeModel.del_yn == "N",
            ReviewNodeModel.rn_u_idx == user_idx,
            ReviewNodeModel.created_at >= start,
            ReviewNodeModel.created_at < end,
        )
        .order_by(ReviewNodeModel.created_at.asc(), ReviewNodeModel.idx.asc())
    )
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
    rn_recall: float | None = None,
    rn_iou: float | None = None,
    rn_overlay_summary: str | None = None,
    rn_findings: str | None = None,
    rn_slice_index: int | None = None,
    state: str = "N",
) -> ReviewNodeModel:
    row = ReviewNodeModel(
        del_yn="N",
        rn_u_idx=rn_u_idx,
        rn_s_idx=rn_s_idx,
        rn_disease=rn_disease,
        rn_image=rn_image,
        rn_solution=rn_solution,
        rn_recall=rn_recall,
        rn_iou=rn_iou,
        rn_overlay_summary=rn_overlay_summary,
        rn_findings=rn_findings,
        rn_slice_index=rn_slice_index,
        state=state,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
