"""마이페이지 정보 수정·비밀번호 변경 전용 DB 처리. 기존 회원 레포지토리·관리자 update_user 는 재사용하지 않는다.

쓰기는 정확히 다음 칼럼만 바꾼다: 프로필 수정 = user_name·user_un·user_sp·updated_at / 비밀번호 변경 = user_pw·updated_at.
아이디·생년월일·권한(mb_level)·승인/탈퇴 표시(state·del_yn)·left_at·가입일은 이 모듈에서 쓰지 않는다. 새 DB 칸·인덱스를 만들지 않는다.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from models.userModel import UserModel


def lock_active_member(db: Session, user_idx: int) -> UserModel | None:
    """활성 회원 한 명의 행을 **잠그고 DB 의 최신 값으로 다시 읽는다**(FOR UPDATE + populate_existing). 쓰기 전 검사와 쓰기가 같은 잠금 안에서 일어난다."""
    stmt = (
        select(UserModel)
        .where(UserModel.idx == user_idx, UserModel.del_yn == "N")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).first()


def apply_profile(db: Session, user: UserModel, name: str, school: str, major: str, updated_at: str) -> None:
    user.user_name = name
    user.user_un = school
    user.user_sp = major
    user.updated_at = updated_at
    db.flush()


def apply_password_hash(db: Session, user: UserModel, password_hash: str, updated_at: str) -> None:
    user.user_pw = password_hash
    user.updated_at = updated_at
    db.flush()
