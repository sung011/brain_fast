from datetime import datetime

from sqlalchemy.orm import Session

from models.studyModel import StudyModel
from repositories import studyRepositories as repositories
from schemas.studySchemas import ST_MODAL_VALUES, ST_PART_VALUES, parse_study_image_paths


def format_updated_at(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def summarize_st_image_for_list(st_image: str | None) -> str:
    """목록용 짧은 표시. 슬라이드 JSON 전체를 내려보내지 않는다."""
    paths = parse_study_image_paths(st_image)
    if paths:
        return f"슬라이드 {len(paths)}장"
    return (st_image or "").strip()


def list_studies(db: Session) -> list[StudyModel]:
    return repositories.list_all(db)


def list_studies_for_admin(db: Session) -> list[dict]:
    """관리자 학습 목록. st_image는 요약만 넣어 응답 크기를 줄인다."""
    rows = repositories.list_all(db)
    return [
        {
            "idx": row.idx,
            "del_yn": row.del_yn,
            "st_part": row.st_part,
            "st_modal": row.st_modal,
            "st_image": summarize_st_image_for_list(row.st_image),
            "st_disease": row.st_disease,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "state": row.state,
        }
        for row in rows
    ]


def get_study(db: Session, idx: int) -> StudyModel | None:
    row = repositories.get_by_idx(db, idx)
    if row is None or row.del_yn == "Y":
        return None
    return row


def create_study(
    db: Session,
    *,
    st_part: str,
    st_modal: str,
    st_disease: str,
    st_image: str,
) -> StudyModel:
    part = (st_part or "").strip()
    modal = (st_modal or "").strip()
    disease = (st_disease or "").strip()
    image = (st_image or "").strip()

    if part not in ST_PART_VALUES:
        raise ValueError("학습 부위(st_part)는 1~4 중 하나여야 합니다.")
    if modal not in ST_MODAL_VALUES:
        raise ValueError("영상 종류(st_modal)는 1~3 중 하나여야 합니다.")
    if not disease:
        raise ValueError("병명(st_disease)을 입력해주세요.")
    if len(disease) > 30:
        raise ValueError("병명은 30자 이하여야 합니다.")
    if not image:
        raise ValueError("이미지 경로가 비어 있습니다.")

    return repositories.create(
        db,
        st_part=part,
        st_modal=modal,
        st_disease=disease,
        st_image=image,
    )


def update_study(
    db: Session,
    idx: int,
    *,
    st_part: str,
    st_modal: str,
    st_disease: str,
    st_image: str | None = None,
) -> StudyModel | None:
    row = get_study(db, idx)
    if row is None:
        return None

    part = (st_part or "").strip()
    modal = (st_modal or "").strip()
    disease = (st_disease or "").strip()
    image = (st_image or "").strip() if st_image is not None else None

    if part not in ST_PART_VALUES:
        raise ValueError("학습 부위(st_part)는 1~4 중 하나여야 합니다.")
    if modal not in ST_MODAL_VALUES:
        raise ValueError("영상 종류(st_modal)는 1~3 중 하나여야 합니다.")
    if not disease:
        raise ValueError("병명(st_disease)을 입력해주세요.")
    if len(disease) > 30:
        raise ValueError("병명은 30자 이하여야 합니다.")
    if image is not None and not image:
        raise ValueError("이미지 경로가 비어 있습니다.")

    return repositories.update(
        db,
        row,
        st_part=part,
        st_modal=modal,
        st_disease=disease,
        st_image=image,
        updated_at=format_updated_at(),
    )


def delete_study(db: Session, idx: int) -> StudyModel | None:
    row = repositories.get_by_idx(db, idx)
    if row is None or row.del_yn == "Y":
        return None
    return repositories.soft_delete(db, row)
