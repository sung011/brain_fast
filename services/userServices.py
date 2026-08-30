from datetime import datetime

from sqlalchemy.orm import Session

from models.userModel import UserModel
from repositories import userRepositories as repositories
from schemas.userSchemas import UserCreate, UserLogin, UserUpdate
from utils.password import hash_password, verify_password

ALLOWED_MB_LEVELS = {1, 10}


def format_updated_at(now: datetime | None = None) -> str:
    """회원 수정일시 문자열 (DB updated_at 컬럼용)."""
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def login(db: Session, body: UserLogin) -> UserModel | None:
    user = repositories.get_by_user_id(db, body.user_id)
    if user is None:
        return None
    if not verify_password(body.user_pw, user.user_pw):
        return None
    return user


def user_all_data(db: Session) -> list[UserModel]:
    return repositories.list_all(db)


def user_id_exists(db: Session, user_id: str) -> bool:
    """아이디가 이미 등록되어 있는지 확인한다 (삭제된 회원 포함)."""
    return repositories.get_by_user_id(db, user_id, active_only=False) is not None


def create_user(db: Session, body: UserCreate) -> UserModel:
    """
    관리자 회원가입으로 회원을 넣는다.
    삭제된 아이디까지 포함해 중복이면 None (409로 이어짐).
    비밀번호는 여기서 해시하고, 권한(mb_level)은 DB 기본값을 쓴다.
    """
    if repositories.get_by_user_id(db, body.user_id, active_only=False):
        return None
    user = repositories.create(
        db,
        user_id=body.user_id,
        user_pw=hash_password(body.user_pw),
        user_name=body.user_name,
        user_birth=body.user_birth,
        user_un=body.user_un,
        user_sp=body.user_sp,
    )
    db.commit()
    return user


def get_user(db: Session, idx: int) -> UserModel | None:
    """활성 회원 한 명을 조회한다."""
    user = repositories.get_by_idx(db, idx)
    if user is None or user.del_yn != "N":
        return None
    return user


def update_user(db: Session, idx: int, body: UserUpdate) -> UserModel | None:
    """회원 정보를 수정한다. 없거나 삭제된 회원이면 None."""
    user = get_user(db, idx)
    if user is None:
        return None

    fields: dict[str, object] = {
        "user_name": body.user_name,
        "user_birth": body.user_birth,
        "user_un": body.user_un,
        "user_sp": body.user_sp,
        "mb_level": body.mb_level,
        "updated_at": format_updated_at(),
    }
    if body.mb_level not in ALLOWED_MB_LEVELS:
        raise ValueError("허용되지 않은 권한입니다.")
    if body.user_pw:
        if len(body.user_pw) < 4:
            raise ValueError("비밀번호는 4자 이상이어야 합니다.")
        fields["user_pw"] = hash_password(body.user_pw)

    repositories.update(db, user, **fields)
    db.commit()
    return user


def delete_user(db: Session, idx: int) -> UserModel | None:
    """회원을 논리 삭제한다. 없거나 이미 삭제된 회원이면 None."""
    user = get_user(db, idx)
    if user is None:
        return None
    repositories.soft_delete(db, user)
    db.commit()
    return user
