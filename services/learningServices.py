from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models.medicalTermsModel import MedicalTermsModel
from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from repositories import learningRepositories as repositories
from repositories import reviewNodeRepositories as review_node_repositories
from repositories import studyRepositories as study_repositories
from schemas.studySchemas import (
    ST_PART_LABELS,
    parse_study_image_paths,
    resolve_st_modal,
    resolve_st_part,
)

KST = timezone(timedelta(hours=9))

ALLOWED_BODY_PARTS = {"brain", "chest", "abdomen", "knee", "other"}
ALLOWED_MODALITIES = {"xray", "ct", "mri", "common"}


def get_by_body_part(
    db: Session,
    region: str,
    *,
    modality: str | None = None,
) -> list[MedicalTermsModel]:
    """부위(body_part)로 의학용어 목록을 조회한다. modality가 있으면 함께 필터한다."""
    part = (region or "").strip().lower()
    if part not in ALLOWED_BODY_PARTS:
        raise ValueError(
            "부위(region)는 brain, chest, abdomen, knee, other 중 하나여야 합니다."
        )

    modal: str | None = None
    if modality is not None and modality.strip():
        modal = modality.strip().lower()
        if modal not in ALLOWED_MODALITIES:
            raise ValueError(
                "영상 종류(type)는 xray, ct, mri, common 중 하나여야 합니다."
            )

    return repositories.get_by_body_part(db, part, modality=modal)


def _normalize_exclude(exclude_idxs: list[int] | None) -> list[int] | None:
    if not exclude_idxs:
        return None
    return [int(x) for x in exclude_idxs]


def _merge_exclude_idxs(
    db: Session,
    *,
    exclude_idxs: list[int] | None = None,
    user_idx: int | None = None,
) -> list[int] | None:
    """프론트 exclude + 해당 사용자가 이미 제출한 study idx를 합친다."""
    merged: list[int] = list(_normalize_exclude(exclude_idxs) or [])
    if user_idx is not None:
        for idx in review_node_repositories.list_solved_study_idxs(db, int(user_idx)):
            if idx not in merged:
                merged.append(idx)
    return merged or None


def get_random_problem(
    db: Session,
    *,
    st_part: str | None = None,
    st_modal: str | None = None,
    exclude_idxs: list[int] | None = None,
    user_idx: int | None = None,
) -> StudyModel | None:
    """학습용 study 문제를 랜덤으로 1건 조회한다.

    st_part/st_modal은 숫자(1~4, 1~3) 또는 이름(brain, chest, CT 등)을 받는다.
    user_idx가 있으면 review_node에 제출한 문제는 제외한다.
    """
    part = resolve_st_part(st_part)
    modal = resolve_st_modal(st_modal)

    return study_repositories.get_random(
        db,
        st_part=part,
        st_modal=modal,
        exclude_idxs=_merge_exclude_idxs(
            db, exclude_idxs=exclude_idxs, user_idx=user_idx
        ),
    )


def _normalize_web_path(path: str) -> str:
    return (path or "").strip().replace("\\", "/")


def _path_basename(path: str) -> str:
    return _normalize_web_path(path).rstrip("/").split("/")[-1]


def _find_slice_index_by_path(paths: list[str], image_path: str) -> int:
    target = _normalize_web_path(image_path)
    if not target:
        raise ValueError("image_path가 비어 있습니다.")

    for i, path in enumerate(paths):
        if _normalize_web_path(path) == target:
            return i

    target_name = _path_basename(target)
    matches = [
        i for i, path in enumerate(paths) if _path_basename(path) == target_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"image_path 파일명이 여러 슬라이스와 일치합니다: {target_name}"
        )
    raise ValueError(
        f"image_path에 해당하는 슬라이스를 찾을 수 없습니다: {image_path}"
    )


def resolve_roi_grade_slice(
    db: Session,
    *,
    study_idx: int | None,
    slice_index: int | None = None,
    image_path: str | None = None,
) -> dict | None:
    """
    MRI 슬라이드 문제에서 제출 슬라이스를 고른다.

    - 단일 영상(st_image 경로 1개): None. slice_index·image_path는 무시.
    - MRI(st_image JSON 배열): slice_index 또는 image_path로 배열 항목을 고른다.
    - 둘 다 없으면 / 범위 밖이면 ValueError.
    """
    if study_idx is None:
        return None

    row = study_repositories.get_by_idx(db, int(study_idx))
    if row is None or row.del_yn != "N":
        return None

    paths = parse_study_image_paths(row.st_image)
    if paths is None:
        return None

    path_value = (image_path or "").strip() or None
    has_index = slice_index is not None
    has_path = path_value is not None
    if not has_index and not has_path:
        raise ValueError(
            "MRI 슬라이드 문제는 slice_index 또는 image_path가 필요합니다."
        )

    resolved_index: int | None = None
    if has_path:
        resolved_index = _find_slice_index_by_path(paths, path_value or "")

    if has_index:
        idx = int(slice_index)  # type: ignore[arg-type]
        if idx < 0 or idx >= len(paths):
            raise ValueError(
                f"slice_index={idx} 가 범위를 벗어났습니다. "
                f"0~{len(paths) - 1} 사이여야 합니다."
            )
        if resolved_index is not None and resolved_index != idx:
            raise ValueError(
                "slice_index와 image_path가 서로 다른 슬라이스를 가리킵니다."
            )
        resolved_index = idx

    assert resolved_index is not None
    return {
        "slice_index": resolved_index,
        "image_path": paths[resolved_index],
        "slice_count": len(paths),
    }


def submit_problem(
    db: Session,
    *,
    study_idx: int,
    answer: str,
    st_part: str | None = None,
    st_modal: str | None = None,
    exclude_idxs: list[int] | None = None,
    user_idx: int | None = None,
) -> dict:
    """
    현재 문제를 채점하고, 제출 후에만 다음 랜덤 문제를 돌려준다.
    화면에서는 제출 성공 응답의 next만 쓰면 새 문제가 바뀐다.
    """
    row = study_repositories.get_by_idx(db, study_idx)
    if row is None or row.del_yn != "N" or row.state != "N":
        raise ValueError("문제를 찾을 수 없습니다.")

    expected = (row.st_disease or "").strip()
    given = (answer or "").strip()
    correct = bool(expected) and expected.casefold() == given.casefold()

    done = list(
        _merge_exclude_idxs(db, exclude_idxs=exclude_idxs, user_idx=user_idx) or []
    )
    if study_idx not in done:
        done.append(study_idx)

    next_row = get_random_problem(
        db,
        st_part=st_part or row.st_part,
        st_modal=st_modal or row.st_modal,
        exclude_idxs=done,
        user_idx=user_idx,
    )

    return {
        "correct": correct,
        "study_idx": study_idx,
        "expected": expected,
        "next": next_row,
        "exclude_idxs": done,
    }


def today_bounds(now: datetime | None = None) -> tuple[date, datetime, datetime]:
    """오늘 00:00 이상, 내일 00:00 미만. now 가 없으면 한국 시각."""
    current = now or datetime.now(KST)
    if current.tzinfo is not None:
        current = current.astimezone(KST).replace(tzinfo=None)
    day = current.date()
    start = datetime.combine(day, datetime.min.time())
    return day, start, start + timedelta(days=1)


def study_minutes(times: list[datetime]) -> int:
    """첫 제출과 마지막 제출 사이의 분. 제출이 1건 이하면 0."""
    if len(times) < 2:
        return 0
    span = max(times) - min(times)
    return int(round(span.total_seconds() / 60))


def summarize_today_parts(
    rows: list[tuple[ReviewNodeModel, StudyModel | None]],
) -> list[dict]:
    """부위별 학습 시간·문제 수·정답률. 오늘 제출이 있는 부위만, 뇌·흉부·복부·무릎 순."""
    grouped: dict[str, list[ReviewNodeModel]] = {code: [] for code in ST_PART_LABELS}
    for review, study in rows:
        part = study.st_part if study is not None else None
        if part not in grouped:
            continue
        grouped[part].append(review)

    items: list[dict] = []
    for code, label in ST_PART_LABELS.items():
        reviews = grouped[code]
        total = len(reviews)
        if total == 0:
            continue
        correct = sum(1 for review in reviews if (review.rn_solution or "").upper() == "C")
        times = [review.created_at for review in reviews if review.created_at is not None]
        rate = int(round(correct * 100 / total)) if total else 0
        items.append(
            {
                "st_part": code,
                "part_label": label,
                "study_minutes": study_minutes(times),
                "problem_count": total,
                "correct_rate": rate,
            }
        )
    return items


def today_learning(db: Session, user_idx: int, *, now: datetime | None = None) -> dict:
    """한 회원의 오늘 학습 현황. 제출은 review_node, 부위는 study.st_part."""
    if user_idx < 1:
        raise ValueError("user_idx가 필요합니다.")
    day, start, end = today_bounds(now)
    rows = review_node_repositories.list_between_for_user(
        db,
        user_idx,
        start=start,
        end=end,
    )
    return {
        "date": day.isoformat(),
        "user_idx": user_idx,
        "items": summarize_today_parts(rows),
    }
