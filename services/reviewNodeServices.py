from __future__ import annotations

import base64
import uuid
from typing import Any

from sqlalchemy.orm import Session

from config import settings
from repositories import reviewNodeRepositories as repositories
from repositories import studyRepositories as study_repositories
from services.nasServices import NasNotConfiguredError, nas_service

SOLUTION_MAP = {
    "정답": "C",
    "부분정답": "H",
    "오답": "W",
}
SOLUTION_LABELS = {
    "C": "정답",
    "H": "부분정답",
    "W": "오답",
}

REVIEW_DIR = "/stylesheets/assets/review"


def solution_code(result: str | None) -> str | None:
    key = (result or "").strip()
    return SOLUTION_MAP.get(key)


def solution_label(code: str | None) -> str:
    key = (code or "").strip().upper()
    return SOLUTION_LABELS.get(key, code or "-")


def _clip_disease(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    return text[:30]


def _overlay_bytes(overlay_b64: str | None) -> bytes | None:
    raw = (overlay_b64 or "").strip()
    if not raw:
        return None
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        data = base64.b64decode(raw)
    except Exception:
        return None
    return data or None


def _disease_for_review(db: Session, study_idx: int | None, grade: dict[str, Any]) -> str | None:
    if study_idx is not None:
        study = study_repositories.get_by_idx(db, study_idx)
        if study is not None:
            disease = _clip_disease(study.st_disease)
            if disease:
                return disease
    classification = grade.get("classification") or {}
    return _clip_disease(
        classification.get("top_label_ko") or classification.get("top_label")
    )


async def save_submission(
    db: Session,
    *,
    image_bytes: bytes,
    image_filename: str,
    grade: dict[str, Any],
    study_idx: int | None = None,
    user_idx: int | None = None,
) -> dict[str, Any]:
    """
    제출 이미지를 NAS review 폴더에 올리고 review_node 행을 만든다.
    경로 예) /web/mu_shop/public/stylesheets/assets/review/{file}
    DB rn_image 예) /stylesheets/assets/review/{file}
    """
    if not nas_service.enabled:
        raise NasNotConfiguredError(
            "NAS가 설정되지 않았습니다. .env 에 NAS_URL, NAS_USER, NAS_PASSWORD 를 넣으세요."
        )

    review_png = grade.get("_review_png")
    if isinstance(review_png, (bytes, bytearray)) and review_png:
        content = bytes(review_png)
        ext = "png"
    else:
        overlay = _overlay_bytes(grade.get("overlay_png_base64"))
        if overlay:
            content = overlay
            ext = "png"
        else:
            content = image_bytes
            name = (image_filename or "review.bin").replace("\\", "/").split("/")[-1]
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else "png"
            if ext not in {"png", "jpg", "jpeg", "webp", "gif", "bmp"}:
                ext = "png"

    if not content:
        raise ValueError("제출 이미지가 비어 있습니다.")

    dest = (settings.nas_review_path or REVIEW_DIR).strip() or REVIEW_DIR
    upload_name = f"{uuid.uuid4().hex[:10]}_{study_idx or 'x'}.{ext}"
    uploaded = await nas_service.upload_bytes(content, upload_name, remote_dir=dest)
    remote_path = uploaded.get("remote_path") or f"{dest}/{upload_name}"
    rn_image = nas_service.to_public_path(remote_path)

    row = repositories.create(
        db,
        rn_u_idx=user_idx,
        rn_s_idx=study_idx,
        rn_disease=_disease_for_review(db, study_idx, grade),
        rn_image=rn_image,
        rn_solution=solution_code(grade.get("result")),
        state="N",
    )
    return {
        "review_idx": row.idx,
        "rn_image": row.rn_image,
        "rn_solution": row.rn_solution,
        "remote_path": remote_path,
    }


def list_for_admin(
    db: Session,
    *,
    user_idx: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """관리자용 풀이 이력. study·회원 정보를 붙여 돌려준다."""
    from schemas.studySchemas import ST_MODAL_LABELS, ST_PART_LABELS

    rows = repositories.list_with_details(db, user_idx=user_idx, limit=limit)
    items: list[dict[str, Any]] = []
    for review, study, user in rows:
        part = study.st_part if study else None
        modal = study.st_modal if study else None
        items.append(
            {
                "idx": review.idx,
                "user_idx": review.rn_u_idx,
                "user_id": user.user_id if user else None,
                "user_name": user.user_name if user else None,
                "study_idx": review.rn_s_idx,
                "st_part": part,
                "st_part_label": ST_PART_LABELS.get(part or "", part),
                "st_modal": modal,
                "st_modal_label": ST_MODAL_LABELS.get(modal or "", modal),
                "st_disease": (study.st_disease if study else None) or review.rn_disease,
                "rn_disease": review.rn_disease,
                "rn_solution": review.rn_solution,
                "rn_solution_label": solution_label(review.rn_solution),
                "rn_image": review.rn_image,
                "created_at": review.created_at,
            }
        )
    return items
