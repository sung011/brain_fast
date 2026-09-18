"""Q&A 문의 (회원 ↔ 관리자)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from models.qaModel import QaMessageModel, QaThreadModel
from repositories import qaRepositories as repositories
from repositories import userRepositories as user_repos


def format_updated_at(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _clip(text: str | None, n: int = 120) -> str:
    raw = (text or "").strip()
    if len(raw) <= n:
        return raw
    return raw[: n - 1] + "…"


def _thread_payload(thread: QaThreadModel, user=None) -> dict[str, Any]:
    return {
        "idx": thread.idx,
        "qt_u_idx": thread.qt_u_idx,
        "user_id": user.user_id if user else None,
        "user_name": user.user_name if user else None,
        "qt_title": thread.qt_title,
        "qt_last_msg": thread.qt_last_msg,
        "qt_last_at": thread.qt_last_at,
        "qt_unread_admin": thread.qt_unread_admin or 0,
        "qt_unread_user": thread.qt_unread_user or 0,
        "created_at": thread.created_at,
        "state": thread.state,
    }


def _message_payload(msg: QaMessageModel) -> dict[str, Any]:
    return {
        "idx": msg.idx,
        "qm_t_idx": msg.qm_t_idx,
        "qm_u_idx": msg.qm_u_idx,
        "qm_role": msg.qm_role,
        "qm_body": msg.qm_body,
        "created_at": msg.created_at,
    }


def list_threads_for_admin(db: Session, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows = repositories.list_threads(db, limit=limit)
    return [_thread_payload(thread, user) for thread, user in rows]


def list_threads_for_user(db: Session, user_idx: int) -> list[dict[str, Any]]:
    rows = repositories.list_threads(db, user_idx=user_idx)
    return [_thread_payload(thread, user) for thread, user in rows]


def admin_unread_total(db: Session) -> int:
    return repositories.count_admin_unread(db)


def get_thread_detail(db: Session, thread_idx: int) -> dict[str, Any] | None:
    thread = repositories.get_thread(db, thread_idx)
    if thread is None or thread.del_yn == "Y":
        return None
    user = user_repos.get_by_idx(db, thread.qt_u_idx) if thread.qt_u_idx else None
    messages = repositories.list_messages(db, thread_idx)
    return {
        "thread": _thread_payload(thread, user),
        "messages": [_message_payload(m) for m in messages],
    }


def create_inquiry(
    db: Session,
    *,
    user_idx: int,
    title: str,
    body: str,
) -> dict[str, Any]:
    title_text = (title or "").strip()
    body_text = (body or "").strip()
    if not title_text:
        raise ValueError("제목이 필요합니다.")
    if not body_text:
        raise ValueError("문의 내용이 필요합니다.")
    if len(title_text) > 100:
        title_text = title_text[:100]

    now = datetime.now()
    thread = repositories.create_thread(
        db,
        qt_u_idx=user_idx,
        qt_title=title_text,
        qt_last_msg=_clip(body_text),
        qt_last_at=now,
        qt_unread_admin=1,
    )
    msg = repositories.create_message(
        db,
        qm_t_idx=thread.idx,
        qm_u_idx=user_idx,
        qm_role="U",
        qm_body=body_text,
    )
    user = user_repos.get_by_idx(db, user_idx)
    return {
        "thread": _thread_payload(thread, user),
        "message": _message_payload(msg),
    }


def add_user_message(
    db: Session,
    *,
    thread_idx: int,
    user_idx: int,
    body: str,
) -> dict[str, Any]:
    thread = repositories.get_thread(db, thread_idx)
    if thread is None or thread.del_yn == "Y":
        raise ValueError("문의 스레드를 찾을 수 없습니다.")
    if thread.qt_u_idx != user_idx:
        raise ValueError("본인 문의만 메시지를 보낼 수 있습니다.")
    if thread.state == "S":
        raise ValueError("종료된 문의입니다.")

    body_text = (body or "").strip()
    if not body_text:
        raise ValueError("메시지 내용이 필요합니다.")

    now = datetime.now()
    msg = repositories.create_message(
        db,
        qm_t_idx=thread.idx,
        qm_u_idx=user_idx,
        qm_role="U",
        qm_body=body_text,
    )
    repositories.update_thread_meta(
        db,
        thread,
        qt_last_msg=_clip(body_text),
        qt_last_at=now,
        qt_unread_admin=(thread.qt_unread_admin or 0) + 1,
        updated_at=format_updated_at(now),
    )
    user = user_repos.get_by_idx(db, user_idx)
    return {
        "thread": _thread_payload(thread, user),
        "message": _message_payload(msg),
    }


def add_admin_message(
    db: Session,
    *,
    thread_idx: int,
    admin_idx: int | None,
    body: str,
) -> dict[str, Any]:
    thread = repositories.get_thread(db, thread_idx)
    if thread is None or thread.del_yn == "Y":
        raise ValueError("문의 스레드를 찾을 수 없습니다.")

    body_text = (body or "").strip()
    if not body_text:
        raise ValueError("메시지 내용이 필요합니다.")

    now = datetime.now()
    msg = repositories.create_message(
        db,
        qm_t_idx=thread.idx,
        qm_u_idx=admin_idx,
        qm_role="A",
        qm_body=body_text,
    )
    repositories.update_thread_meta(
        db,
        thread,
        qt_last_msg=_clip(body_text),
        qt_last_at=now,
        qt_unread_user=(thread.qt_unread_user or 0) + 1,
        qt_unread_admin=0,
        updated_at=format_updated_at(now),
    )
    user = user_repos.get_by_idx(db, thread.qt_u_idx) if thread.qt_u_idx else None
    return {
        "thread": _thread_payload(thread, user),
        "message": _message_payload(msg),
    }


def mark_admin_read(db: Session, thread_idx: int) -> dict[str, Any] | None:
    thread = repositories.get_thread(db, thread_idx)
    if thread is None or thread.del_yn == "Y":
        return None
    repositories.update_thread_meta(
        db,
        thread,
        qt_unread_admin=0,
        updated_at=format_updated_at(),
    )
    user = user_repos.get_by_idx(db, thread.qt_u_idx) if thread.qt_u_idx else None
    return _thread_payload(thread, user)


def mark_user_read(db: Session, thread_idx: int, user_idx: int) -> dict[str, Any] | None:
    thread = repositories.get_thread(db, thread_idx)
    if thread is None or thread.del_yn == "Y":
        return None
    if thread.qt_u_idx != user_idx:
        raise ValueError("본인 문의만 확인할 수 있습니다.")
    repositories.update_thread_meta(
        db,
        thread,
        qt_unread_user=0,
        updated_at=format_updated_at(),
    )
    user = user_repos.get_by_idx(db, user_idx)
    return _thread_payload(thread, user)


def close_thread(db: Session, thread_idx: int) -> dict[str, Any] | None:
    thread = repositories.get_thread(db, thread_idx)
    if thread is None or thread.del_yn == "Y":
        return None
    repositories.update_thread_meta(
        db,
        thread,
        state="S",
        updated_at=format_updated_at(),
    )
    user = user_repos.get_by_idx(db, thread.qt_u_idx) if thread.qt_u_idx else None
    return _thread_payload(thread, user)
