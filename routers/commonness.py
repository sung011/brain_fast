"""
학습자(앱/웹)용 일반 JSON API 라우터.

prefix 없음. 주소 예: /login, /qa, /learning/problem

하는 일:
- /health/db          : DB 연결 확인
- /login              : 학습자 로그인
- /popups             : 메인 화면 홍보 팝업 목록
- /qa ...             : 문의 생성·목록·상세·추가 메시지
- /glossary           : 의학용어 사전 조회
- /learning/problem   : 랜덤 학습 문제 1건
- /learning/problem/{idx}/image : 문제 영상 한 장 (NAS 에서 대신 받아 줌)
- /learning/problem/{idx} : 문제 번호로 1건 (오답노트 '다시 풀기', 정답 숨김)
- /learning/reviews  : 내 오답노트 목록 (기본: 오답·부분정답)
- /learning/reviews/{idx} : 오답 상세 + 다시 풀 문제
- /learning/submit    : 병명 제출·채점 + 다음 문제
- /learning/roi-grade : 이미지+ROI 제출 채점 (MRI는 slice_index/image_path, 리뷰 저장 포함)
- /items/{item_id}    : (연습용) 입력값 검증 테스트

입력값이 잘못되면 exception_handlers 가 422와 로그를 남긴다.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import settings
from db import get_db
from db import ping_db
from models.studyModel import StudyModel
from repositories import studyRepositories as study_repositories
from schemas.popupSchemas import PopupPublicOut
from schemas.qaSchemas import QaCreateBody, QaMessageBody
from schemas.userSchemas import UserLogin
from services import learningServices as learning_service
from services import popupServices as popup_service
from services import qaServices as qa_service
from services import reviewNodeServices as review_node_service
from services import roiGradeServices as roi_grade_service
from services import userServices as user_service
from utils.qa_sse import qa_events
from utils.review_sse import review_events

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
    user_idx: int | None = Field(
        None, description="로그인 사용자 idx. 있으면 제출 이력 문제 제외"
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


def _popup_public_url(pp_image: str | None) -> str | None:
    if not pp_image:
        return None
    path = pp_image if pp_image.startswith("/") else f"/{pp_image}"
    base = (settings.nas_public_base_url or "").rstrip("/")
    if not base:
        return path
    return f"{base}{path}"


@router.get("/popups", response_model=list[PopupPublicOut])
def public_popups(db: Session = Depends(get_db)):
    """
    학습자 메인용 활성 홍보 팝업 목록.
    del_yn=N, state=N, 노출 기간 안인 항목만 pp_sort 순으로 반환.
    """
    rows = popup_service.list_public(db)
    return [
        PopupPublicOut(
            idx=row.idx,
            pp_title=row.pp_title,
            pp_image=row.pp_image,
            pp_image_url=_popup_public_url(row.pp_image),
            pp_link=row.pp_link,
            pp_sort=row.pp_sort or 0,
        )
        for row in rows
    ]


def _qa_dt(value):
    if value is not None and hasattr(value, "isoformat"):
        return value.isoformat(sep=" ", timespec="seconds")
    return value


def _qa_thread_public(item: dict) -> dict:
    out = dict(item)
    out["qt_last_at"] = _qa_dt(out.get("qt_last_at"))
    out["created_at"] = _qa_dt(out.get("created_at"))
    return out


def _qa_message_public(item: dict) -> dict:
    out = dict(item)
    out["created_at"] = _qa_dt(out.get("created_at"))
    return out


@router.post("/qa")
async def qa_create(body: QaCreateBody, db: Session = Depends(get_db)):
    """학습자 문의 생성 (새 스레드 + 첫 메시지)."""
    try:
        result = qa_service.create_inquiry(
            db,
            user_idx=body.user_idx,
            title=body.title,
            body=body.body,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    payload = {
        "thread": _qa_thread_public(result["thread"]),
        "message": _qa_message_public(result["message"]),
        "unread_total": qa_service.admin_unread_total(db),
    }
    await qa_events.publish("qa_created", payload)
    return {"ok": True, **payload}


@router.get("/qa")
def qa_list_for_user(
        user_idx: int = Query(..., description="회원 user_member.idx"),
        db: Session = Depends(get_db),
):
    """학습자 본인 문의 스레드 목록."""
    rows = qa_service.list_threads_for_user(db, user_idx)
    return {
        "ok": True,
        "items": [_qa_thread_public(row) for row in rows],
    }


@router.get("/qa/{idx}")
def qa_detail_for_user(
        idx: int,
        user_idx: int = Query(..., description="회원 user_member.idx"),
        db: Session = Depends(get_db),
):
    """학습자 본인 문의 상세. 조회 시 회원 미읽음 초기화."""
    detail = qa_service.get_thread_detail(db, idx)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="문의 스레드를 찾을 수 없습니다.",
        )
    thread = detail["thread"]
    if thread.get("qt_u_idx") != user_idx:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="본인 문의만 조회할 수 있습니다.",
        )
    try:
        marked = qa_service.mark_user_read(db, idx, user_idx)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    return {
        "ok": True,
        "thread": _qa_thread_public(marked or thread),
        "messages": [_qa_message_public(m) for m in detail["messages"]],
    }


@router.post("/qa/{idx}/messages")
async def qa_user_reply(
        idx: int,
        body: QaMessageBody,
        db: Session = Depends(get_db),
):
    """학습자 추가 메시지."""
    if body.user_idx is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_idx가 필요합니다.",
        )
    try:
        result = qa_service.add_user_message(
            db,
            thread_idx=idx,
            user_idx=body.user_idx,
            body=body.body,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    payload = {
        "thread": _qa_thread_public(result["thread"]),
        "message": _qa_message_public(result["message"]),
        "unread_total": qa_service.admin_unread_total(db),
    }
    await qa_events.publish("qa_message", payload)
    return {"ok": True, **payload}


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
        user_idx: int | None = Query(
            default=None,
            description="로그인 사용자 idx. review_node 제출 이력 제외",
        ),
        db: Session = Depends(get_db),
):
    """
    study 테이블에서 랜덤 문제 1건.
    - st_part: 1~4 또는 brain, chest, abdomen, knee
    - st_modal: 1~3 또는 xray, ct, mri
    - user_idx: 있으면 해당 사용자가 이미 제출한 문제 제외
    예) /learning/problem?st_part=chest&st_modal=CT&user_idx=3
    화면에서는 시작 시 1번만 호출하고, 제출 전에는 다시 호출하지 않는다.
    다음 문제는 POST /learning/submit 응답의 next를 사용한다.
    """
    try:
        row = learning_service.get_random_problem(
            db,
            st_part=st_part,
            st_modal=st_modal,
            exclude_idxs=exclude_idxs,
            user_idx=user_idx,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if row is None:
        detail = "조건에 맞는 학습 문제가 없습니다."
        if user_idx is not None:
            detail = "풀지 않은 학습 문제가 더 없습니다. (이미 제출한 문제는 제외됩니다)"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        )

    return {"ok": True, "problem": _problem_payload(row)}


@router.get("/learning/problem/{study_idx}")
def learning_problem_by_idx(study_idx: int, db: Session = Depends(get_db)):
    """
    문제 번호로 1건. 오답노트의 '다시 풀기'에 쓴다. 정답(st_disease)은 숨긴다.
    삭제·비활성 문제면 404.
    """
    row = learning_service.get_problem(db, study_idx)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="학습 문제를 찾을 수 없습니다.")
    return {"ok": True, "problem": _problem_payload(row)}


@router.get("/learning/reviews")
def learning_reviews(
        user_idx: int = Query(..., description="로그인 사용자 idx"),
        solution: str | None = Query(
            None, description="결과 코드 C(정답)·H(부분정답)·W(오답)를 쉼표로. all 이면 전체. 기본 W,H"
        ),
        limit: int = Query(50, ge=1, le=200),
        db: Session = Depends(get_db),
):
    """내 오답노트 목록. 최신순. 기본은 오답·부분정답만."""
    try:
        solutions = review_node_service.parse_solutions(solution)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    items = review_node_service.list_for_learner(db, user_idx, solutions=solutions, limit=limit)
    return {"ok": True, "count": len(items), "items": items}


@router.get("/learning/reviews/{review_idx}")
def learning_review_detail(
        review_idx: int,
        user_idx: int = Query(..., description="로그인 사용자 idx. 본인 기록만 볼 수 있다"),
        db: Session = Depends(get_db),
):
    """오답 상세. problem 이 있으면 그 문제를 다시 풀 수 있다 (정답 제외)."""
    item = review_node_service.get_for_learner(db, review_idx, user_idx)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="풀이 기록을 찾을 수 없습니다.")
    return {"ok": True, "review": item}


@router.get("/learning/problem/{study_idx}/image")
async def learning_problem_image(
        study_idx: int,
        slice_index: int = Query(0, ge=0, description="MRI 슬라이스 순번(0부터). 단일 영상은 0"),
        db: Session = Depends(get_db),
):
    """
    문제 영상 한 장을 돌려준다.
    NAS 공개 주소에서 서버가 대신 받아 주므로, 학습자 화면(Vue)이 NAS 의 CORS 설정과 상관없이
    영상을 받아 그대로 /learning/roi-grade 에 올릴 수 있다.
    """
    try:
        content, media_type, filename = await learning_service.fetch_problem_image(
            db, study_idx, slice_index
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except learning_service.ProblemImageError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=600",
            # 한글 파일 이름은 헤더(latin-1)에 그대로 못 넣으므로 RFC 5987 형식으로 보낸다
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        },
    )


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
            user_idx=body.user_idx,
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
    """학습자 로그인. 성공 시 idx, user_id, user_name, mb_level 을 돌려준다."""
    user = user_service.login(db, body)
    # 응답에는 비밀번호(입력값·해시)를 넣지 않는다. 실패 원인(아이디/비밀번호)도 구분하지 않는다.
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다.",
        )
    return {
        "ok": True,
        "idx": user.idx,
        "user_id": user.user_id,
        "user_name": user.user_name,
        "mb_level": user.mb_level,
    }


@router.post("/learning/roi-grade")
async def learning_roi_grade(
        db: Session = Depends(get_db),
        image: UploadFile = File(..., description="원본 이미지 파일 그대로"),
        roi_type: str = Form(..., description="box 또는 circle"),
        x: float | None = Form(None, description="박스 왼쪽 x"),
        y: float | None = Form(None, description="박스 위쪽 y"),
        width: float | None = Form(None, description="박스 너비"),
        height: float | None = Form(None, description="박스 높이"),
        cx: float | None = Form(None, description="원 중심 x"),
        cy: float | None = Form(None, description="원 중심 y"),
        radius: float | None = Form(None, description="원 반지름"),
        normalized: bool = Form(
            True,
            description="true면 좌표는 이미지 대비 0~1, false면 픽셀",
        ),
        include_overlay: bool = Form(
            True,
            description="이상이 있으면 해당 부위를 색칠한 PNG(base64)를 포함",
        ),
        study_idx: int | None = Form(None, description="현재 문제 study.idx"),
        user_idx: int | None = Form(None, description="제출 사용자 user_member.idx"),
        slice_index: int | None = Form(
            None,
            description=(
                    "MRI 제출 슬라이스 순번(0-based). "
                    "CT/단일 영상은 생략 또는 0. MRI는 image_path와 둘 중 하나 이상 필요"
            ),
        ),
        image_path: str | None = Form(
            None,
            description=(
                    "MRI 제출 슬라이스 웹 경로. "
                    "예: /stylesheets/assets/brain/MRI/....png. "
                    "slice_index와 둘 중 하나 이상 있으면 됨"
            ),
        ),
        st_part: str | None = Form(
            None,
            description=(
                    "부위(brain, chest 또는 1~4). 다음 문제와 채점 모델 선택에 쓴다. "
                    "study_idx가 있으면 study 행 값이 우선"
            ),
        ),
        st_modal: str | None = Form(
            None,
            description="영상 종류(xray, ct, mri 또는 1~3). study_idx가 있으면 study 행 값이 우선",
        ),
):
    """
    이미지 + ROI(박스/원) 제출 채점.

    - 이미지에 이상 부위가 있으면 overlay_png_base64 로 색칠해서 돌려준다.
    - 사용자 ROI가 그 위치에 있으면 정답, 비슷하면 부분정답, 아니면 오답.
    - MRI(st_image JSON 배열)는 study_idx + slice_index 또는 image_path로
      제출 슬라이스를 고른 뒤, 올린 image/좌표로 채점한다.
    - CT/단일 영상은 기존과 동일. slice_index·image_path는 optional.
    - 흉부(st_part=2) X-ray 문제는 흉부 모델로 채점한다. 흉부 모델이 준비되지 않았으면 503.
    - 제출 이미지는 사용자 ROI(박스/원)를 그린 뒤
      NAS /web/mu_shop/public/stylesheets/assets/review 에 저장하고
      review_node.rn_image 에 웹 경로를 넣는다.
    - NAS 가 꺼져 있거나 업로드에 실패해도 채점 결과와 review_node 기록은 남긴다.
      이때 image_saved=false, image_error 에 이유가 들어간다.
    - user_idx + study_idx 를 넣으면 제출 이력이 남아
      이후 GET /learning/problem?user_idx=... 에서 같은 문제가 제외된다.
    - next: 방금 푼 문제를 제외한 다음 랜덤 문제(없으면 null).
    """
    try:
        learning_service.resolve_roi_grade_slice(
            db,
            study_idx=study_idx,
            slice_index=slice_index,
            image_path=image_path,
        )
        grade_part, grade_modal = learning_service.resolve_roi_grade_target(
            db,
            study_idx=study_idx,
            st_part=st_part,
            st_modal=st_modal,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    data = await image.read()
    try:
        result = roi_grade_service.grade_image_roi(
            data,
            image.filename or "upload.png",
            roi_type=roi_type,
            normalized=normalized,
            x=x,
            y=y,
            width=width,
            height=height,
            cx=cx,
            cy=cy,
            radius=radius,
            include_overlay=include_overlay,
            st_part=grade_part,
            st_modal=grade_modal,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    try:
        saved = await review_node_service.save_submission(
            db,
            image_bytes=data,
            image_filename=image.filename or "upload.png",
            grade=result,
            study_idx=study_idx,
            user_idx=user_idx,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        # DB 오류 내용은 응답에 넣지 않는다 (서버 로그에는 예외 처리기가 남긴다)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="채점 기록을 저장하지 못했습니다. 잠시 후 다시 제출해 주세요.",
        ) from exc
    finally:
        result.pop("_review_png", None)

    result.update(saved)
    # 제출한 뒤에만 정답 병명을 보여 준다 (문제 화면에서는 숨김)
    if study_idx is not None:
        answered = study_repositories.get_by_idx(db, int(study_idx))
        result["expected_disease"] = answered.st_disease if answered else None

    await review_events.publish(
        "review_created",
        {
            "review_idx": saved.get("review_idx"),
            "user_idx": user_idx,
            "study_idx": study_idx,
            "rn_image": saved.get("rn_image"),
            "rn_solution": saved.get("rn_solution"),
        },
    )

    next_row = None
    if study_idx is not None:
        part = st_part
        modal = st_modal
        if part is None or modal is None:
            current_study = study_repositories.get_by_idx(db, int(study_idx))
            if current_study is not None:
                part = part or current_study.st_part
                modal = modal or current_study.st_modal
        try:
            next_row = learning_service.get_random_problem(
                db,
                st_part=part,
                st_modal=modal,
                exclude_idxs=[int(study_idx)],
                user_idx=user_idx,
            )
        except ValueError:
            next_row = None

    result["next"] = _problem_payload(next_row) if next_row else None
    return result
