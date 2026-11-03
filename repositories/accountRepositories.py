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

