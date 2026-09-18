"""학습자 메인 홍보 팝업."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from models.popupModel import PopupModel
from repositories import popupRepositories as repositories


def format_updated_at(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _parse_dt(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    # datetime-local: 2026-09-18T15:00
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError("날짜 형식이 올바르지 않습니다. 예) 2026-09-18T15:00")


def list_admin(db: Session) -> list[PopupModel]:
    return repositories.list_all(db, active_only=False)


def list_public(db: Session) -> list[PopupModel]:
    return repositories.list_public_active(db)


def get_popup(db: Session, idx: int) -> PopupModel | None:
    row = repositories.get_by_idx(db, idx)
    if row is None or row.del_yn == "Y":
        return None
    return row


def create_popup(
    db: Session,
    *,
    pp_title: str | None,
    pp_image: str,
    pp_link: str | None = None,
    pp_sort: int = 0,
    start_at: str | None = None,
    end_at: str | None = None,
    state: str = "N",
) -> PopupModel:
    image = (pp_image or "").strip()
    if not image:
        raise ValueError("팝업 이미지가 필요합니다.")
    st = (state or "N").strip().upper()
    if st not in {"N", "S"}:
        raise ValueError("state는 N(노출) 또는 S(숨김)이어야 합니다.")
    start = _parse_dt(start_at)
    end = _parse_dt(end_at)
    if start and end and end < start:
        raise ValueError("종료 시각이 시작 시각보다 빠를 수 없습니다.")
    return repositories.create(
        db,
        pp_title=(pp_title or "").strip() or None,
        pp_image=image,
        pp_link=(pp_link or "").strip() or None,
        pp_sort=int(pp_sort or 0),
        start_at=start,
        end_at=end,
        state=st,
    )


def update_popup(
    db: Session,
    idx: int,
    *,
    pp_title: str | None,
    pp_link: str | None = None,
    pp_sort: int = 0,
    start_at: str | None = None,
    end_at: str | None = None,
    state: str = "N",
    pp_image: str | None = None,
) -> PopupModel | None:
    row = get_popup(db, idx)
    if row is None:
        return None
    st = (state or "N").strip().upper()
    if st not in {"N", "S"}:
        raise ValueError("state는 N(노출) 또는 S(숨김)이어야 합니다.")
    start = _parse_dt(start_at)
    end = _parse_dt(end_at)
    if start and end and end < start:
        raise ValueError("종료 시각이 시작 시각보다 빠를 수 없습니다.")
    image = (pp_image or "").strip() or None
    return repositories.update(
        db,
        row,
        pp_title=(pp_title or "").strip() or None,
        pp_link=(pp_link or "").strip() or None,
        pp_sort=int(pp_sort or 0),
        start_at=start,
        end_at=end,
        state=st,
        pp_image=image,
        updated_at=format_updated_at(),
    )


def delete_popup(db: Session, idx: int) -> PopupModel | None:
    row = get_popup(db, idx)
    if row is None:
        return None
    return repositories.soft_delete(db, row)
