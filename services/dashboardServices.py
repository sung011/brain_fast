"""관리자 대시보드 통계."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import cast, func, select
from sqlalchemy.orm import Session
from sqlalchemy.types import Date

from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from models.userModel import UserModel
from schemas.studySchemas import ST_MODAL_LABELS, ST_PART_LABELS
from services.reviewNodeServices import list_for_admin, solution_label

KST = timezone(timedelta(hours=9))


def _today_kst() -> date:
    return datetime.now(KST).date()


def get_stats(db: Session) -> dict[str, Any]:
    """대시보드용 요약·차트·최근 풀이. 빠른 조회 위주."""
    today = _today_kst()
    today_start = datetime.combine(today, datetime.min.time())
    week_start = datetime.combine(today - timedelta(days=6), datetime.min.time())

    user_count = int(
        db.scalar(
            select(func.count())
            .select_from(UserModel)
            .where(UserModel.del_yn == "N")
        )
        or 0
    )
    study_count = int(
        db.scalar(
            select(func.count())
            .select_from(StudyModel)
            .where(StudyModel.del_yn == "N", StudyModel.state == "N")
        )
        or 0
    )

    # 채점 비율 1회 집계 → total / correct / C·H·W
    solution_rows = db.execute(
        select(ReviewNodeModel.rn_solution, func.count())
        .where(ReviewNodeModel.del_yn == "N")
        .group_by(ReviewNodeModel.rn_solution)
    ).all()
    solution_map = {code or "-": int(cnt) for code, cnt in solution_rows}
    total_reviews = sum(solution_map.values())
    correct_count = solution_map.get("C", 0)
    today_reviews = int(
        db.scalar(
            select(func.count())
            .select_from(ReviewNodeModel)
            .where(
                ReviewNodeModel.del_yn == "N",
                ReviewNodeModel.created_at >= today_start,
            )
        )
        or 0
    )
    correct_rate = (
        round(correct_count * 100.0 / total_reviews, 1) if total_reviews else 0.0
    )

    solution_breakdown = [
        {
            "code": code,
            "label": solution_label(code),
            "count": solution_map.get(code, 0),
        }
        for code in ("C", "H", "W")
    ]
    other = sum(
        cnt for code, cnt in solution_map.items() if code not in {"C", "H", "W"}
    )
    if other:
        solution_breakdown.append({"code": "-", "label": "기타", "count": other})

    # 최근 7일 추이 — 쿼리 1번
    day_rows = db.execute(
        select(
            cast(ReviewNodeModel.created_at, Date).label("day"),
            func.count(),
        )
        .where(
            ReviewNodeModel.del_yn == "N",
            ReviewNodeModel.created_at >= week_start,
        )
        .group_by(cast(ReviewNodeModel.created_at, Date))
    ).all()
    day_map = {}
    for row in day_rows:
        key = row[0]
        if hasattr(key, "date") and callable(key.date):
            key = key.date()
        day_map[key] = int(row[1])
    day_labels: list[str] = []
    day_counts: list[int] = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        day_labels.append(f"{d.month}/{d.day}")
        day_counts.append(day_map.get(d, 0))

    part_modal_rows = db.execute(
        select(StudyModel.st_part, StudyModel.st_modal, func.count())
        .where(StudyModel.del_yn == "N", StudyModel.state == "N")
        .group_by(StudyModel.st_part, StudyModel.st_modal)
        .order_by(func.count().desc())
    ).all()
    study_by_part_modal = [
        {
            "st_part": part,
            "st_modal": modal,
            "part_label": ST_PART_LABELS.get(part or "", part or "-"),
            "modal_label": ST_MODAL_LABELS.get(modal or "", modal or "-"),
            "count": int(cnt),
        }
        for part, modal, cnt in part_modal_rows
    ]

    recent = list_for_admin(db, limit=10)
    for item in recent:
        created = item.get("created_at")
        if created is not None and hasattr(created, "isoformat"):
            item["created_at"] = created.isoformat(sep=" ", timespec="seconds")

    return {
        "cards": {
            "user_count": user_count,
            "study_count": study_count,
            "today_reviews": today_reviews,
            "correct_rate": correct_rate,
            "total_reviews": total_reviews,
            "correct_count": correct_count,
        },
        "trend": {
            "labels": day_labels,
            "counts": day_counts,
        },
        "solution": solution_breakdown,
        "study_by_part_modal": study_by_part_modal,
        "recent_reviews": recent,
    }


def quick_health() -> dict[str, Any]:
    """네트워크 호출 없이 DB ping + NAS 설정 여부만."""
    from db import ping_db
    from services.nasServices import nas_service

    health: dict[str, Any] = {
        "db": {"ok": False, "name": None},
        "nas": {"ok": False, "enabled": False},
    }
    try:
        health["db"] = {"ok": True, "name": ping_db()}
    except Exception as exc:
        health["db"] = {"ok": False, "name": None, "error": str(exc)}

    configured = bool(nas_service.enabled)
    health["nas"] = {
        # 대시보드에서는 로그인 시도하지 않음 (느림). 설정 여부만.
        "ok": configured,
        "enabled": configured,
        "error": None if configured else "미설정",
    }
    return health
