"""마이페이지(본인 프로필 + 최근 저장된 문제 제출 5건). 읽기만 한다.

회원은 요청이 아니라 **서버 세션의 learner** 로만 정한다. 요청이 낸 회원 번호·쿼리·화면 저장소 값은 쓰지 않는다.
관리자 세션(session["user"])·게스트 쿠키는 학습자 권한이 아니다. 이 서비스는 DB 에 쓰지 않고 영상·저장소를 부르지 않는다.
"""
from sqlalchemy.orm import Session

from repositories import accountProfileRepositories as repositories
from schemas.accountProfileSchemas import MeProfile, MeResponse, RecentLearning, RecentSubmission
from schemas.studySchemas import ST_MODAL_LABELS, ST_PART_LABELS

RECENT_LIMIT = 5
_SOLUTION_LABELS = {"C": "정답", "H": "부분정답", "W": "오답"}
NO_RESULT_LABEL = "결과 없음"


class NotAuthenticated(Exception):
    """학습자 세션이 없거나 값이 잘못됐거나 회원이 없다/탈퇴했다 → 401."""


class NotApproved(Exception):
    """학습자 세션은 있으나 관리자 승인 표시(email_verified)가 정확히 True 가 아니다 → 403."""


def _learner_idx(session: object) -> tuple[int, bool]:
    learner = session.get("learner") if isinstance(session, dict) else None
    if not isinstance(learner, dict):
        raise NotAuthenticated
    idx = learner.get("idx")
    if isinstance(idx, bool) or not isinstance(idx, int) or idx <= 0:
        raise NotAuthenticated
    return idx, learner.get("email_verified") is True


def _created_text(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat(timespec="seconds")   # DB 에 기록된 그대로. 시간대를 붙이거나 시차를 더하지 않는다.
    return str(value)


def submission_dto(row) -> RecentSubmission:
    """제출 한 행 → 최소 DTO. 문제가 삭제·누락이면 부위·영상 종류를 비우고 study_available=False."""
    available = row.study_found is not None and row.study_del_yn == "N"
    code = str(row.solution or "").strip().upper()
    solution = code if code in _SOLUTION_LABELS else None
    return RecentSubmission(
        review_idx=row.review_idx,
        study_idx=row.study_idx,
        part_label=ST_PART_LABELS.get(str(row.st_part or "")) if available else None,
        modal_label=ST_MODAL_LABELS.get(str(row.st_modal or "")) if available else None,
        study_available=available,
        solution=solution,
        solution_label=_SOLUTION_LABELS.get(solution, NO_RESULT_LABEL),   # 미채점·알 수 없는 값을 오답으로 보지 않는다
        created_at=_created_text(row.created_at),
    )


def get_me(db: Session, session: object) -> MeResponse:
    idx, approved = _learner_idx(session)
    user = repositories.get_active_member(db, idx)
    if user is None:
        raise NotAuthenticated
    if not approved:
        raise NotApproved
    rows = repositories.recent_submissions(db, user.idx, RECENT_LIMIT)
    return MeResponse(
        profile=MeProfile(idx=user.idx, user_id=user.user_id, user_name=user.user_name, user_un=user.user_un, user_sp=user.user_sp),
        recent_learning=RecentLearning(limit=RECENT_LIMIT, items=[submission_dto(row) for row in rows]),
    )
