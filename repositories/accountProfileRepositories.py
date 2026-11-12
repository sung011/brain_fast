"""마이페이지(본인 조회) 전용 DB 읽기. 읽기만 한다 — insert/update/commit 을 하지 않는다."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from models.userModel import UserModel


def get_active_member(db: Session, user_idx: int) -> UserModel | None:
    """탈퇴하지 않은 회원 한 명. 없으면 None."""
    stmt = select(UserModel).where(UserModel.idx == user_idx, UserModel.del_yn == "N")
    return db.scalars(stmt).first()


def recent_submissions(db: Session, user_idx: int, limit: int):
    """그 회원이 저장한 문제 제출을 최신순으로 limit 건. 본인·삭제 안 된 제출·문제가 있는 제출만 SQL 에서 거른다.

    - 문제 없는 화면 분석 기록(rn_s_idx 비어 있음)은 제외한다.
    - 문제가 삭제·누락돼도 제출 행은 남긴다(LEFT JOIN). 부위·영상 종류는 활성 문제에서만 읽는다.
    - 정렬: 저장 시각 최신 → 같은 시각이면 번호가 큰 것 먼저. 전체를 가져와 브라우저에서 자르지 않는다.
    """
    stmt = (
        select(
            ReviewNodeModel.idx.label("review_idx"),
            ReviewNodeModel.rn_s_idx.label("study_idx"),
            ReviewNodeModel.rn_solution.label("solution"),
            ReviewNodeModel.created_at.label("created_at"),
            StudyModel.idx.label("study_found"),
            StudyModel.del_yn.label("study_del_yn"),
            StudyModel.st_part.label("st_part"),
            StudyModel.st_modal.label("st_modal"),
        )
        .select_from(ReviewNodeModel)
        .outerjoin(StudyModel, StudyModel.idx == ReviewNodeModel.rn_s_idx)
        .where(
            ReviewNodeModel.rn_u_idx == user_idx,
            ReviewNodeModel.del_yn == "N",
            ReviewNodeModel.rn_s_idx.is_not(None),
        )
        .order_by(ReviewNodeModel.created_at.desc().nulls_last(), ReviewNodeModel.idx.desc())
        .limit(limit)
    )
    return db.execute(stmt).all()
