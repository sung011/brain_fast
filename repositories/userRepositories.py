from sqlalchemy import select
from sqlalchemy.orm import Session

from models.userModel import UserModel


def get_by_idx(db: Session, idx: int) -> UserModel | None:
    """PK(idx)로 회원 한 명을 조회한다."""
    return db.get(UserModel, idx)


def get_by_user_id(
        db: Session,
        user_id: str,
        *,
        active_only: bool = True,
) -> UserModel | None:
    """로그인 아이디(user_id)로 회원을 조회한다."""
    stmt = select(UserModel).where(UserModel.user_id == user_id)

    if active_only:
        stmt = stmt.where(UserModel.del_yn == "N")
    return db.scalars(stmt).first()


def get_by_user(db: Session, user_id: str, user_pw: str) -> UserModel | None:
    """아이디·비밀번호로 회원을 조회한다 (삭제되지 않은 회원만)."""
    stmt = select(UserModel).where(
        UserModel.user_id == user_id,
        UserModel.user_pw == user_pw,
        UserModel.del_yn == "N",
    )
    return db.scalars(stmt).first()


def list_all(db: Session, *, active_only: bool = True) -> list[UserModel]:
    """회원 목록을 최신 가입순으로 조회한다."""
    stmt = select(UserModel).order_by(UserModel.created_at.desc())
    if active_only:
        stmt = stmt.where(
            UserModel.del_yn == "N",
            UserModel.state == "N",
            UserModel.user_id != "admin",
        )
    return list(db.scalars(stmt).all())


def create(
        db: Session,
        *,
        user_id: str,
        user_pw: str,
        user_birth: str,
        user_un: str,
        user_sp: str,
        user_name: str | None = None,
        mb_level: int = 1,
        state: str = "N",
) -> UserModel:
    """회원을 새로 등록한다."""
    user = UserModel(
        user_id=user_id,
        user_pw=user_pw,
        user_name=user_name,
        user_birth=user_birth,
        user_un=user_un,
        user_sp=user_sp,
        mb_level=mb_level,
        state=state,
    )
    db.add(user)
    db.flush()
    db.refresh(user)
    return user


def update(db: Session, user: UserModel, **fields: object) -> UserModel:
    """전달된 필드만 회원 정보를 수정한다."""
    for key, value in fields.items():
        setattr(user, key, value)
    db.flush()
    db.refresh(user)
    return user


def soft_delete(db: Session, user: UserModel) -> UserModel:
    """회원을 논리 삭제한다 (del_yn = 'Y')."""
    user.del_yn = "Y"
    user.state = "S"
    db.flush()
    db.refresh(user)
    return user
