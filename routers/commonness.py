"""
일반 API 라우터.

JSON을 주고받는 엔드포인트. 입력값이 잘못되면
exception_handlers.validation_exception_handler 가 422와 로그를 남긴다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db import get_db
from db import ping_db
from models.studyModel import StudyModel
from schemas.userSchemas import UserLogin
from services import learningServices as learning_service
from services import userServices as user_service
from utils.password import hash_password, verify_password

router = APIRouter(tags=["commonness"])


class LearningSubmitBody(BaseModel):
    study_idx: int = Field(..., description="현재 문제 idx")
    answer: str = Field(..., description="제출 병명(st_disease)")
    st_part: str | None = Field(
        None, description="다음 문제 부위. 1~4 또는 brain/chest/abdomen/knee"
    )
    st_modal: str | None = Field(
        None, description="다음 문제 영상 종류. 1~3 또는 xray/ct/mri"
    )
    exclude_idxs: list[int] = Field(
        default_factory=list,
        description="이미 푼 문제 idx 목록. 다음 문제에서 제외",
    )


def _problem_payload(row: StudyModel) -> dict:
    """문제 화면용. 정답(st_disease)은 제출 전까지 숨긴다."""
    return {
        "idx": row.idx,
        "st_part": row.st_part,
        "st_modal": row.st_modal,
        "st_image": row.st_image,
    }


@router.get("/health/db")
def health_db():
    """PostgreSQL 연결 확인. 연결된 데이터베이스 이름을 돌려준다."""
    return {"ok": True, "database": ping_db()}


@router.get("/items/{item_id}")
async def read_item(item_id: int, q: str | None = None):
    """
    아이템 조회.
    - item_id: 경로의 숫자. 숫자가 아니면 422 입력값 오류가 나고 로그에 남는다.
    - q: 선택 쿼리 문자열. 예) /items/1?q=test
    """
    return {"item_id": item_id, "q": q}


@router.get("/glossary")
def glossary(
        region: str | None = None,
        type: str | None = None,
        db: Session = Depends(get_db),
):
    """
    의학용어 사전 조회.
    - region: 부위. 예) /glossary?region=brain
    - type: 영상 종류(modality). 예) /glossary?region=brain&type=ct
      (ct/xray/mri 요청 시 해당 값 + common 용어를 함께 반환)
    """
    if not region or not region.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="region이 필요합니다. 예) /glossary?region=brain",
        )
    try:
        rows = learning_service.get_by_body_part(db, region, modality=type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    items = [
        {
            "id": row.id,
            "term_id": row.term_id,
            "name_ko": row.name_ko,
            "name_en": row.name_en,
            "definition_ko": row.definition_ko,
            "body_part": row.body_part,
            "tags": row.tags,
            "tags_ko": row.tags_ko,
            "modality": row.modality,
            "category": row.category,
        }
        for row in rows
    ]
    return {
        "ok": True,
        "region": region.strip().lower(),
        "type": type.strip().lower() if type and type.strip() else None,
        "count": len(items),
        "items": items,
    }


@router.get("/learning/problem")
def learning_problem(
    st_part: str | None = None,
    st_modal: str | None = None,
    exclude_idxs: list[int] | None = Query(
        default=None,
        description="이미 푼 idx. 예) exclude_idxs=1&exclude_idxs=2",
    ),
    db: Session = Depends(get_db),
):
    """
    study 테이블에서 랜덤 문제 1건.
    - st_part: 1~4 또는 brain, chest, abdomen, knee
    - st_modal: 1~3 또는 xray, ct, mri
    예) /learning/problem?st_part=chest&st_modal=CT
    화면에서는 시작 시 1번만 호출하고, 제출 전에는 다시 호출하지 않는다.
    다음 문제는 POST /learning/submit 응답의 next를 사용한다.
    """
    try:
        row = learning_service.get_random_problem(
            db,
            st_part=st_part,
            st_modal=st_modal,
            exclude_idxs=exclude_idxs,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="조건에 맞는 학습 문제가 없습니다.",
        )
    return {"ok": True, "problem": _problem_payload(row)}


@router.post("/learning/submit")
def learning_submit(body: LearningSubmitBody, db: Session = Depends(get_db)):
    """
    현재 문제 제출·채점.
    제출 후에만 next(다음 랜덤 문제)를 돌려준다.
    """
    try:
        result = learning_service.submit_problem(
            db,
            study_idx=body.study_idx,
            answer=body.answer,
            st_part=body.st_part,
            st_modal=body.st_modal,
            exclude_idxs=body.exclude_idxs,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    next_row: StudyModel | None = result["next"]
    return {
        "ok": True,
        "correct": result["correct"],
        "study_idx": result["study_idx"],
        "expected": result["expected"],
        "exclude_idxs": result["exclude_idxs"],
        "next": _problem_payload(next_row) if next_row else None,
    }


@router.post("/login")
def login_post(body: UserLogin, db: Session = Depends(get_db)):
    user = user_service.login(db, body)

    # 개발 확인용 응답. 나중에 제거하세요.
    debug = {
        "plain_pw": body.user_pw,
        "hashed_pw": hash_password(body.user_pw),
        "db_user_pw": user.user_pw if user else None,
        "pw_match": (
            verify_password(body.user_pw, user.user_pw)
            if user and user.user_pw.startswith("$2")
            else None
        ),
    }
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"message": "아이디 또는 비밀번호가 올바르지 않습니다.", **debug},
        )
    return {
        "ok": True,
        "idx": user.idx,
        "user_id": user.user_id,
        "user_name": user.user_name,
        "mb_level": user.mb_level,
        **debug,
    }
