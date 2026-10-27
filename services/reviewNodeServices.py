from __future__ import annotations

import base64
import logging
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from config import settings
from repositories import reviewNodeRepositories as repositories
from services.objectStorageServices import StorageNotConfiguredError, storage_service

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


def _finding_score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score"))
    except (TypeError, ValueError):
        return float("-inf")


def _ratio(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return round(number, 4)


def _finding_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "label": item.get("label"),
                "label_ko": item.get("label_ko"),
                "score": _ratio(item.get("score")),
            }
        )
    return items


def _summary_group(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "top_label": value.get("top_label"),
        "top_label_ko": value.get("top_label_ko"),
        "confidence": _ratio(value.get("confidence")),
        "findings": _finding_items(value.get("findings")),
    }


def grade_findings_json(grade: dict[str, Any]) -> str | None:
    """결과 요약에 다시 쓸 출혈·germinoma 소견만 JSON으로 남긴다."""
    payload: dict[str, Any] = {}
    classification = _summary_group(grade.get("classification"))
    germinoma = _summary_group(grade.get("germinoma"))
    if classification:
        payload["classification"] = classification
    if germinoma:
        payload["germinoma"] = germinoma
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=False)


def load_findings_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def highest_percent_disease(grade: dict[str, Any]) -> str | None:
    """출혈·germinoma 후보 중 score가 가장 높은 병명. 한글 이름을 우선한다."""
    candidates: list[dict[str, Any]] = []
    classification = grade.get("classification") or {}
    if isinstance(classification, dict):
        findings = classification.get("findings") or []
        if isinstance(findings, list):
            candidates.extend(item for item in findings if isinstance(item, dict))
    germinoma = grade.get("germinoma") or {}
    if isinstance(germinoma, dict):
        g_findings = germinoma.get("findings") or []
        if isinstance(g_findings, list) and g_findings:
            candidates.extend(item for item in g_findings if isinstance(item, dict))
        elif germinoma.get("confidence") is not None:
            candidates.append(
                {
                    "label": germinoma.get("top_label"),
                    "label_ko": germinoma.get("top_label_ko"),
                    "score": germinoma.get("confidence"),
                }
            )
    if candidates:
        best = max(candidates, key=_finding_score)
        if _finding_score(best) != float("-inf"):
            return _clip_disease(best.get("label_ko") or best.get("label"))
    if isinstance(classification, dict):
        return _clip_disease(
            classification.get("top_label_ko") or classification.get("top_label")
        )
    return None


async def save_submission(
    db: Session,
    *,
    image_bytes: bytes,
    image_filename: str,
    grade: dict[str, Any],
    study_idx: int | None = None,
    user_idx: int | None = None,
    slice_index: int | None = None,
) -> dict[str, Any]:
    """
    제출 이미지를 Object Storage review 경로에 올리고 review_node 행을 만든다.
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
    if not storage_service.enabled:
        image_error = "Object Storage가 설정되지 않아 제출 이미지를 저장하지 못했습니다."
    else:
        dest = (settings.nas_review_path or REVIEW_DIR).strip() or REVIEW_DIR
        upload_name = f"{uuid.uuid4().hex[:10]}_{study_idx or 'x'}.{ext}"
        try:
            uploaded = await storage_service.upload_bytes(content, upload_name, remote_dir=dest)
            remote_path = uploaded.get("remote_path") or f"{dest}/{upload_name}"
            rn_image = storage_service.to_public_path(remote_path)
        except Exception as exc:
            logger.warning(
                "리뷰 이미지 업로드 실패 (study=%s): %s",
                study_idx,
                exc.__class__.__name__,
            )
            image_error = "이미지 업로드에 실패해 제출 이미지를 저장하지 못했습니다."

    row = repositories.create(
        db,
        rn_u_idx=user_idx,
        rn_s_idx=study_idx,
        rn_disease=highest_percent_disease(grade),
        rn_image=rn_image,
        rn_solution=solution_code(grade.get("result")),
        rn_recall=_ratio(grade.get("recall")),
        rn_iou=_ratio(grade.get("iou")),
        rn_overlay_summary=(
            grade.get("overlay_summary").strip()
            if isinstance(grade.get("overlay_summary"), str) and grade.get("overlay_summary").strip()
            else None
        ),
        rn_findings=grade_findings_json(grade),
        rn_slice_index=slice_index if slice_index is not None and slice_index >= 0 else None,
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
PART_REGION = {
    "1": "brain",
    "2": "chest",
    "3": "abdomen",
    "4": "knee",
}
MODAL_TYPE = {
    "1": "x-ray",
    "2": "CT",
    "3": "MRI",
}
NOTE_SOLUTIONS = {"W", "H"}


def list_wrong_notes(db: Session, user_idx: int) -> list[dict[str, Any]]:
    """문제(study)마다 가장 최근 제출만 본다. 그 결과가 오답·부분정답일 때만 노트에 넣는다."""
    if user_idx < 1:
        raise ValueError("user_idx는 1 이상이어야 합니다.")
    notes: list[dict[str, Any]] = []
    seen_studies: set[int | tuple[str, int | None]] = set()
    for row in list_for_admin(db, user_idx=user_idx):
        study_idx = row.get("study_idx")
        key: int | tuple[str, int | None] = (
            study_idx if study_idx is not None else ("review", row.get("idx"))
        )
        if key in seen_studies:
            continue
        seen_studies.add(key)
        code = str(row.get("rn_solution") or "").strip().upper()
        if code not in NOTE_SOLUTIONS:
            continue
        part = str(row.get("st_part") or "")
        modal = str(row.get("st_modal") or "")
        created = row.get("created_at")
        if created is not None and hasattr(created, "isoformat"):
            created = created.isoformat(sep=" ", timespec="seconds")
        notes.append(
            {
                "idx": row.get("idx"),
                "study_idx": study_idx,
                "disease": row.get("st_disease") or row.get("rn_disease") or "",
                "solution": code,
                "solution_label": row.get("rn_solution_label") or solution_label(code),
                "part_label": row.get("st_part_label") or "",
                "modal_label": row.get("st_modal_label") or "",
                "region": PART_REGION.get(part, ""),
                "type": MODAL_TYPE.get(modal, ""),
                "image": row.get("rn_image") or "",
                "created_at": created,
            }
        )
    return notes


def get_saved_review(db: Session, review_idx: int, user_idx: int) -> dict[str, Any] | None:
    """제출 번호로 그 사용자의 채점 이미지를 한 건 돌려준다."""
    if review_idx < 1 or user_idx < 1:
        raise ValueError("idx와 user_idx는 1 이상이어야 합니다.")
    found = repositories.get_owned(db, review_idx, user_idx)
    if found is None:
        return None
    from schemas.studySchemas import ST_MODAL_LABELS, ST_PART_LABELS

    review, study = found
    part = str(study.st_part if study else "")
    modal = str(study.st_modal if study else "")
    code = str(review.rn_solution or "").strip().upper()
    return {
        "idx": review.idx,
        "study_idx": review.rn_s_idx,
        "disease": (study.st_disease if study else None) or review.rn_disease or "",
        "solution": code,
        "solution_label": solution_label(code),
        "part_label": ST_PART_LABELS.get(part, ""),
        "modal_label": ST_MODAL_LABELS.get(modal, ""),
        "region": PART_REGION.get(part, ""),
        "type": MODAL_TYPE.get(modal, ""),
        "image": review.rn_image or "",
        "study_image": (study.st_image if study else None) or "",
        "recall": _ratio(review.rn_recall),
        "iou": _ratio(review.rn_iou),
        "overlay_summary": review.rn_overlay_summary or "",
        "findings": load_findings_json(review.rn_findings),
        "slice_index": review.rn_slice_index,
    }


def list_learning_history(db: Session, user_idx: int) -> dict[str, Any]:
    """그 사용자의 제출을 최신순으로 모두 돌려준다. 정답·부분정답·오답을 빼지 않는다."""
    if user_idx < 1:
        raise ValueError("user_idx는 1 이상이어야 합니다.")
    from repositories import studyRepositories as study_repositories

    items: list[dict[str, Any]] = []
    for row in list_for_admin(db, user_idx=user_idx):
        study_idx = row.get("study_idx")
        code = str(row.get("rn_solution") or "").strip().upper()
        part = str(row.get("st_part") or "")
        modal = str(row.get("st_modal") or "")
        created = row.get("created_at")
        if created is not None and hasattr(created, "isoformat"):
            created = created.isoformat(sep=" ", timespec="seconds")
        items.append(
            {
                "idx": row.get("idx"),
                "study_idx": study_idx,
                "disease": row.get("st_disease") or row.get("rn_disease") or "",
                "solution": code,
                "solution_label": row.get("rn_solution_label") or solution_label(code),
                "part_label": row.get("st_part_label") or "",
                "modal_label": row.get("st_modal_label") or "",
                "region": PART_REGION.get(part, ""),
                "type": MODAL_TYPE.get(modal, ""),
                "image": row.get("rn_image") or "",
                "created_at": created,
            }
        )
    return {
        "total_studies": study_repositories.count_active(db),
        "items": items,
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
