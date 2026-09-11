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
    idx: int
    st_part: str | None = None
    st_modal: str | None = None
    st_disease: str | None = None
    st_image: str | None = None
    remote_path: str | None = None


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
