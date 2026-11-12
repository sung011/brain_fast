"""계정찾기 전용 DB 처리. 기존 회원 레포지토리는 수정하지 않는다."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.userModel import UserModel


def find_ids(db: Session, user_name: str, user_birth: str) -> list[str]:
    stmt = select(UserModel.user_id).where(
        UserModel.user_name == user_name,
        UserModel.user_birth == user_birth,
        UserModel.del_yn == "N",
    ).order_by(UserModel.idx)
    return list(db.scalars(stmt).all())


def find_member(db: Session, user_id: str, user_name: str, user_birth: str) -> UserModel | None:
    stmt = select(UserModel).where(
        UserModel.user_id == user_id,
        UserModel.user_name == user_name,
        UserModel.user_birth == user_birth,
        UserModel.del_yn == "N",
    )
    return db.scalars(stmt).first()


def set_password(db: Session, user: UserModel, password_hash: str, updated_at: str) -> None:
    user.user_pw = password_hash
    user.updated_at = updated_at
    db.flush()



def lock_member_for_reset(db: Session, user_id: str, user_name: str, user_birth: str) -> UserModel | None:
    """비밀번호 재설정용: 신원이 맞는 활성 회원 행을 **잠그고 DB 최신 값으로 다시 읽는다**(FOR UPDATE + populate_existing).
    답 행 조회는 이 잠금 **뒤에** 한다(잠금 순서: 회원 → 답)."""
    stmt = (
        select(UserModel)
        .where(
            UserModel.user_id == user_id,
            UserModel.user_name == user_name,
            UserModel.user_birth == user_birth,
            UserModel.del_yn == "N",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).first()
