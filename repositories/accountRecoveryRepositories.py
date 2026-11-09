"""비밀번호 찾기 질문 답(user_recovery_answer) 전용 DB 처리. 기존 회원 레포지토리는 수정하지 않는다.

잠금 순서는 항상 **회원 행(FOR UPDATE) → 답 행**이다. 답 조회는 populate_existing 으로 미리 읽어 둔 객체가 있어도 최신 값으로 덮는다.
쓰기 대상은 이 테이블뿐이다(회원 행 쓰기는 각 서비스가 기존 레포지토리로 한다).
"""
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models.userRecoveryAnswerModel import UserRecoveryAnswerModel as Answer
from schemas.accountRecoverySchemas import NORM_VERSION, QUESTION_SET


def answers_for(db: Session, user_idx: int) -> dict[str, Answer]:
    """회원의 답 행을 질문 ID 별로 돌려준다. 같은 질문 ID 가 두 번 나오면(제약이 없는 잘못된 스키마) 전체를 무효({})로 본다."""
    stmt = (
        select(Answer)
        .where(Answer.ura_u_idx == user_idx)
        .order_by(Answer.idx)
        .execution_options(populate_existing=True)
    )
    result: dict[str, Answer] = {}
    for row in db.scalars(stmt).all():
        if row.ura_question_id in result:
            return {}
        result[row.ura_question_id] = row
    return result


def insert_answers(db: Session, user_idx: int, hashes: dict[str, str], updated_at: str | None = None) -> None:
    for question_id in sorted(hashes):
        db.add(Answer(
            ura_u_idx=user_idx,
            ura_question_id=question_id,
            ura_answer_hash=hashes[question_id],
            ura_question_set=QUESTION_SET,
            ura_norm_version=NORM_VERSION,
            updated_at=updated_at,
        ))
    db.flush()


def replace_answers(db: Session, user_idx: int, hashes: dict[str, str], updated_at: str) -> None:
    """기존 답을 모두 지우고 새 3행을 넣는다. 호출자는 회원 행을 먼저 잠근 상태여야 한다. commit 은 호출자가 한다."""
    db.execute(delete(Answer).where(Answer.ura_u_idx == user_idx).execution_options(synchronize_session=False))
    db.flush()
    insert_answers(db, user_idx, hashes, updated_at)
