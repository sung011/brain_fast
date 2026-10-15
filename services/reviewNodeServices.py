from __future__ import annotations

import base64
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from config import settings
from repositories import reviewNodeRepositories as repositories
from repositories import studyRepositories as study_repositories
from services.nasServices import nas_service

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
# 학습자 오답노트 기본 필터: 오답·부분정답
WRONG_NOTE_SOLUTIONS = ["W", "H"]

logger = logging.getLogger(__name__)


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

    NAS 가 설정되지 않았거나 업로드에 실패해도 review_node 행은 만든다(rn_image 는 비움).
    그림 하나 때문에 채점 결과와 오답 기록을 잃지 않기 위해서다. 결과의 image_saved / image_error 로 알린다.
    """

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

    rn_image = None
    remote_path = None
    image_error = None
    if not nas_service.enabled:
        image_error = "NAS가 설정되지 않아 제출 이미지를 저장하지 못했습니다."
    else:
        dest = (settings.nas_review_path or REVIEW_DIR).strip() or REVIEW_DIR
        upload_name = f"{uuid.uuid4().hex[:10]}_{study_idx or 'x'}.{ext}"
        try:
            uploaded = await nas_service.upload_bytes(content, upload_name, remote_dir=dest)
            remote_path = uploaded.get("remote_path") or f"{dest}/{upload_name}"
            rn_image = nas_service.to_public_path(remote_path)
        except Exception as exc:  # NAS 오류 내용은 로그에만 남긴다
            logger.warning("리뷰 이미지 NAS 업로드 실패 (study=%s): %s", study_idx, exc.__class__.__name__)
            image_error = "NAS 업로드에 실패해 제출 이미지를 저장하지 못했습니다."

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
        "image_saved": rn_image is not None,
        "image_error": image_error,
    }


def parse_solutions(value: str | None) -> list[str] | None:
    """'W,H' -> ['W', 'H'], 'all' -> None(전체). 없으면 오답노트 기본값(W,H). 모르는 코드는 ValueError."""
    if value is None or not value.strip():
        return list(WRONG_NOTE_SOLUTIONS)
    if value.strip().lower() == "all":
        return None
    codes = [part.strip().upper() for part in value.split(",") if part.strip()]
    unknown = [c for c in codes if c not in SOLUTION_LABELS]
    if unknown or not codes:
        raise ValueError("solution 은 C, H, W 를 쉼표로 잇거나 all 이어야 합니다.")
    return codes


def _learner_item(review, study) -> dict[str, Any]:
    from schemas.studySchemas import ST_MODAL_LABELS, ST_PART_LABELS
    from services.learningServices import problem_image_url

    part = study.st_part if study else None
    modal = study.st_modal if study else None
    return {
        "review_idx": review.idx,
        "study_idx": review.rn_s_idx,
        "st_part": part,
        "st_part_label": ST_PART_LABELS.get(part or "", part),
        "st_modal": modal,
        "st_modal_label": ST_MODAL_LABELS.get(modal or "", modal),
        # 이미 제출한 문제라 정답 병명을 보여 줘도 된다
        "disease": (study.st_disease if study else None) or review.rn_disease,
        "solution": review.rn_solution,
        "solution_label": solution_label(review.rn_solution),
        "image_url": problem_image_url(review.rn_image) if review.rn_image else None,
        "created_at": review.created_at.isoformat() if review.created_at else None,
    }


def list_for_learner(
    db: Session,
    user_idx: int,
    *,
    solutions: list[str] | None,
    limit: int | None = 50,
) -> list[dict[str, Any]]:
    """학습자 본인의 오답노트(기본: 오답·부분정답) 목록."""
    rows = repositories.list_for_user(db, user_idx, solutions=solutions, limit=limit)
    return [_learner_item(review, study) for review, study in rows]


def get_for_learner(db: Session, review_idx: int, user_idx: int) -> dict[str, Any] | None:
    """오답 상세. 다시 풀 수 있는 문제면 problem(정답 제외)도 붙인다. 본인 기록이 아니면 None."""
    found = repositories.get_for_user(db, review_idx, user_idx)
    if found is None:
        return None
    review, study = found
    item = _learner_item(review, study)
    active = study is not None and study.del_yn == "N" and study.state == "N"
    item["problem"] = (
        {"idx": study.idx, "st_part": study.st_part, "st_modal": study.st_modal, "st_image": study.st_image}
        if active else None
    )
    return item


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
