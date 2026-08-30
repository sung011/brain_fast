import bcrypt


def hash_password(plain: str) -> str:
    """평문 비밀번호를 bcrypt 해시로 변환한다."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """평문 비밀번호와 해시가 일치하는지 확인한다."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
