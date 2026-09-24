"""학습(study) API 스키마."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StudyOut(BaseModel):
    idx: int
    del_yn: str
    st_part: str | None = None
    st_modal: str | None = None
    st_image: str | None = None
    st_disease: str | None = None
    created_at: datetime | None = None
    updated_at: str | None = None
    state: str = "N"

    model_config = ConfigDict(from_attributes=True)


class StudyCreateResult(BaseModel):
    ok: bool = True
    idx: int | None = None
    st_part: str | None = None
    st_modal: str | None = None
    st_disease: str | None = None
    st_image: str | None = None
    remote_path: str | None = None
    filename: str | None = None
    error: str | None = None


class StudyBatchCreateResult(BaseModel):
    """학습 다중 등록 결과."""
    ok: bool = True
    count: int = 0
    failed: int = 0
    items: list[StudyCreateResult] = []
    message: str | None = None


# 학습 다중 등록 한도
STUDY_BATCH_MAX_FILES = 50
STUDY_BATCH_MAX_BYTES = 20 * 1024 * 1024  # 파일당 20MB


class StudyUpdateResult(BaseModel):
    ok: bool = True
    idx: int
    st_part: str | None = None
    st_modal: str | None = None
    st_disease: str | None = None
    st_image: str | None = None
    remote_path: str | None = None


# UI·검증용 라벨
ST_PART_LABELS = {
    "1": "뇌",
    "2": "흉부",
    "3": "복부",
    "4": "무릎",
}
ST_MODAL_LABELS = {
    "1": "X-ray",
    "2": "CT",
    "3": "MRI",
}

# NAS/웹 폴더명 (부위·영상 종류)
ST_PART_FOLDERS = {
    "1": "brain",
    "2": "thorax",
    "3": "Abdomen",
    "4": "Knee",
}
ST_MODAL_FOLDERS = {
    "1": "X-ray",
    "2": "CT",
    "3": "MRI",
}

ST_PART_VALUES = set(ST_PART_LABELS)
ST_MODAL_VALUES = set(ST_MODAL_LABELS)

# API에서 brain/chest/CT 같은 이름으로 올 때 DB 코드(1~4, 1~3)로 변환
ST_PART_ALIASES = {
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "brain": "1",
    "chest": "2",
    "thorax": "2",
    "abdomen": "3",
    "knee": "4",
    "뇌": "1",
    "흉부": "2",
    "복부": "3",
    "무릎": "4",
}
ST_MODAL_ALIASES = {
    "1": "1",
    "2": "2",
    "3": "3",
    "xray": "1",
    "x-ray": "1",
    "ct": "2",
    "mri": "3",
}


def resolve_st_part(value: str | None) -> str | None:
    """부위 이름/코드를 study.st_part(1~4)로 변환한다."""
    if value is None:
        return None
    key = value.strip()
    if not key:
        return None
    code = ST_PART_ALIASES.get(key.lower()) or ST_PART_ALIASES.get(key)
    if code is None:
        raise ValueError(
            "부위(st_part)는 1~4 또는 brain, chest, abdomen, knee 중 하나여야 합니다."
        )
    return code


def resolve_st_modal(value: str | None) -> str | None:
    """영상 종류 이름/코드를 study.st_modal(1~3)로 변환한다."""
    if value is None:
        return None
    key = value.strip()
    if not key:
        return None
    code = ST_MODAL_ALIASES.get(key.lower())
    if code is None:
        raise ValueError(
            "영상 종류(st_modal)는 1~3 또는 xray, ct, mri 중 하나여야 합니다."
        )
    return code


ASSETS_ROOT = "/stylesheets/assets"


def build_remote_dir(st_part: str, st_modal: str | None = None) -> str:
    """
    부위·영상 종류 → 웹 상대 저장 경로.
    예) 뇌+CT → /stylesheets/assets/brain/CT
    """
    part = (st_part or "").strip()
    modal = (st_modal or "").strip()
    part_folder = ST_PART_FOLDERS.get(part)
    if not part_folder:
        return ASSETS_ROOT
    path = f"{ASSETS_ROOT}/{part_folder}"
    modal_folder = ST_MODAL_FOLDERS.get(modal)
    if modal_folder:
        path = f"{path}/{modal_folder}"
    return path
