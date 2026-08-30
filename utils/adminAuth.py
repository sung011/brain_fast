"""관리자 로그인 세션 검사 (APIRouter dependencies / Depends 용)."""

from typing import Any

from fastapi import HTTPException, Request, status


LOGIN_PATH = "/admin/login"
PUBLIC_PATHS = {"/admin/login", "/admin/logout"}


def is_admin_public_path(path: str) -> bool:
    """로그인 없이 접근 가능한 관리자 경로인지 확인한다."""
    normalized = path.rstrip("/") or "/"
    return normalized in PUBLIC_PATHS


def admin_session_guard(request: Request) -> dict[str, Any] | None:
    """
    관리자 세션이 없으면 로그인 페이지로 보낸다.
    /admin/login, /admin/logout 은 검사하지 않는다.
    """
    if is_admin_public_path(request.url.path):
        return None

    user = request.session.get("user")
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": LOGIN_PATH},
        )
    return user
