from sqlalchemy.orm import Session

from models.medicalTermsModel import MedicalTermsModel
from models.studyModel import StudyModel
from repositories import learningRepositories as repositories
from repositories import studyRepositories as study_repositories
from schemas.studySchemas import resolve_st_modal, resolve_st_part

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


def get_random_problem(
    db: Session,
    *,
    st_part: str | None = None,
    st_modal: str | None = None,
    exclude_idxs: list[int] | None = None,
) -> StudyModel | None:
    """학습용 study 문제를 랜덤으로 1건 조회한다.

    st_part/st_modal은 숫자(1~4, 1~3) 또는 이름(brain, chest, CT 등)을 받는다.
    """
    part = resolve_st_part(st_part)
    modal = resolve_st_modal(st_modal)

    return study_repositories.get_random(
        db,
        st_part=part,
        st_modal=modal,
        exclude_idxs=_normalize_exclude(exclude_idxs),
    )


def submit_problem(
    db: Session,
    *,
    study_idx: int,
    answer: str,
    st_part: str | None = None,
    st_modal: str | None = None,
    exclude_idxs: list[int] | None = None,
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

    done = list(_normalize_exclude(exclude_idxs) or [])
    if study_idx not in done:
        done.append(study_idx)

    next_row = get_random_problem(
        db,
        st_part=st_part or row.st_part,
        st_modal=st_modal or row.st_modal,
        exclude_idxs=done,
    )

    return {
        "correct": correct,
        "study_idx": study_idx,
        "expected": expected,
        "next": next_row,
        "exclude_idxs": done,
    }
