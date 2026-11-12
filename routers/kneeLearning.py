"""
무릎 학습 ROI 채점 API (뇌의 POST /learning/roi-grade 와 같은 자리, 무릎 전용).

prefix 없음. 주소: POST /learning/roi-grade/knee
같은 파일에 무릎 학습 화면용 주소 두 개가 더 있다(무릎 전용, 공용 문제 API·로그인·관리자 화면은 건드리지 않는다):
- GET /learn/knee        : 학습 화면(HTML). ?demo=1 시연(서버 채점 아님), ?study_idx=번호 실제 연결
- GET /learning/knee/task: 그 화면이 대표 영상을 열 때 쓰는 문제 제시(knee_task). 영상 공급자 연결 전에는 닫혀 있다
여러 장 열람(MSN-20261015-002, 뇌처럼 휠·슬라이더로 장을 넘겨 보기)을 위한 무릎 전용 주소 두 개가 더 있다. 열람은 채점과 별개다:
채점 스위치·기준 ROI 표와 무관하게, **열람 자산 등록부**(class/knee/series_assets.py, 기본 비어 있음)에 등록된 문제의 장만 보여 준다.
- GET /learning/knee/series?study_idx=N                       : 등록된 장 목록(장 수·장별 크기·인증 영상 주소·series_id). 정답·경로·해시는 없다
- GET /learning/knee/series/{study_idx}/{series_id}/image/{n} : 한 장(서버가 목록·장별 해시로 파일을 고르고 검증한 바이트를 그대로 준다)
- GET /learning/knee/random?exclude=N                        : 등록된 케이스(MRI 한 묶음) 중 하나를 무작위로 고른다(번호와 후보 수만)
게스트 실제 영상 열람(MSN-20261016-002, 로그인 없이 탐색·박스 연습만, 채점·저장 없음, 기본 꺼짐 KNEE_GUEST_VIEW_ENABLED):
- POST /learning/knee/guest/session · /session/end, GET /learning/knee/guest/random · /series · /series/{study_idx}/{series_id}/image/{n}
채점은 그대로 대표 1장 기준이다(다른 장은 미채점·저장 0). 대표 기준을 다른 장에 복사하지 않는다.
하는 일: 학습자가 그린 박스(ROI, 이미지 전체를 0~1 로 본 좌표)를 그 문제의 "승인된 기준 ROI"와 IoU 로 비교해
정답/부분정답/오답을 낸다(D-069: IoU≥0.5 / 0.2≤IoU<0.5 / 그 미만). 판정은 정수 픽셀 수로만 한다.

안전 규칙(Codex 재검증 MSN-20260930-001 반영):
- 기본은 꺼져 있다. KNEE_ROI_GRADE_ENABLED=1 일 때만 동작한다.
- 로그인한 학습자(세션 `learner`)만 쓸 수 있다. 사용자 번호는 세션에서만 얻고 요청 값은 믿지 않는다.
  관리자 로그인이 채우는 `session["user"]` 는 학습자로 쓰지 않는다. 이메일 인증이 끝난 사용자만 쓸 수 있다(D-033).
  세션 learner 에 idx(양의 정수)와 email_verified(정확히 True)가 있어야 한다. 이 세션은 학습자 로그인(`POST /login`, 공용)이 성공할 때
  만든다. **발표 한정 관리자 승인 예외**(사용자 결정 2026-10-13, DECISIONS·AUTH_SESSION_CONTRACT 7장): 관리자(팀)가 직접 만든 활성
  회원이 로그인에 성공하면 승인된 학습자로 보고 `email_verified=True` 를 넣는다. 이 값은 이메일 인증 완료가 아니라 관리자 승인 표시다.
  DB 인증 칸·auth_version 재확인·로그아웃은 없다(발표 후 서비스를 이어가면 원래 D-033 로 되돌린다).
- study_idx 는 필수이고, 활성화된 무릎 MRI 문제여야 한다. 촬영면은 요청이 아니라 기준 ROI(서버 기록)에서 얻는다.
- 도형은 박스만 받는다(사용자 결정 2026-10-06). 원은 400 으로 거절하고, 박스는 이미지 안에 있어야 한다.
- 문제 이미지가 한 장이면 단일 이미지 흐름이다. 여러 장(슬라이드, st_image 가 JSON 배열)이면 **대표 슬라이스 흐름**이다:
  서버가 정한 대표 1장만 채점하고(요청의 slice_index·problem_token 으로 제시 버전과 결속), 나머지 장은 graded=false
  (no_reference_for_slice)이다. 판단은 services/kneeSliceGradeServices.py 에 있다(설계 REPRESENTATIVE_SLICE_CONTRACT.md).
- 이미지는 받지 않는다(업로드 이미지를 저장하지 않는다, D-068). AI 모델도 쓰지 않는다.
- 채점하지 않는 경우(승인된 기준 ROI 없음·대표 아님·토큰 문제·이미지 확인 불가)는 graded=false 로 답하고 저장·이벤트를 만들지 않는다.
- 채점되면 review_node 에 그 문제의 단일 이미지 경로(서버가 가진 값, 슬라이드면 대표 장의 경로)를 넣어 저장한다. NAS 에 새로 올리지 않는다.
계산은 services/kneeRoiGradeServices.py, 기준 ROI 는 class/knee/reference_rois.py(단일)·slide_references.py(슬라이드).
승인표는 둘 다 비어 있고, 슬라이드 승인 등록은 데이터 권리 확인과 검수 절차가 끝난 뒤 별도 승인으로 한다.
"""
from __future__ import annotations

import hmac
import logging
import re
import secrets
import time
from importlib import import_module
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from db import get_db
from models.studyModel import StudyModel
from repositories import reviewNodeRepositories as review_node_repositories
from repositories import studyRepositories as study_repositories
from services import kneeRoiGradeServices as knee_grade
from services import kneeSliceGradeServices as slice_grade
from services import learningServices as learning_service
from services import reviewNodeServices as review_node_service
from services import userServices as user_service
from utils.review_sse import review_events

router = APIRouter(tags=["knee-learning"])
_log = logging.getLogger("knee_api")

ST_PART_KNEE = "4"   # study.st_part: 무릎
ST_MODAL_MRI = "3"   # study.st_modal: MRI


def _setting(name: str, default: str) -> str:
    return import_module("class.knee.config").get_setting(name, default)


def _learner_idx(request: Request, db: Session) -> int:
    """세션 `learner` 의 로그인 사용자 번호. 로그인 안 했거나 DB 에 없는(삭제된) 회원이면 401, 이메일 미인증이면 403.

    학습자 세션 키는 `session["learner"]` 로 분리한다(설계 8장). 관리자 로그인이 채우는 `session["user"]` 는 학습자로 쓰지 않는다.
    세션 값만 믿지 않고 DB 에서 회원이 실제로 있는지 다시 확인한다.

    구현되지 않은 것: 이메일 인증 완료 시각·auth_version 의 DB 재확인(user_member 에 그 열이 없다). 그래서 세션의 email_verified(정확히
    True)만 본다. 이 값은 발표 한정 관리자 승인 예외의 승인 표시이며 이메일 인증 완료가 아니다. 세션은 `POST /login` 성공 때 만들어지고,
    이 함수는 요청마다 DB 에서 회원이 실제로 있고 삭제되지 않았는지 다시 확인한다.
    """
    session = request.scope.get("session")
    learner = session.get("learner") if isinstance(session, dict) else None
    idx = learner.get("idx") if isinstance(learner, dict) else None
    if not isinstance(idx, int) or isinstance(idx, bool) or idx <= 0:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    if user_service.get_user(db, idx) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    if learner.get("email_verified") is not True:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="이메일 인증을 마친 뒤 사용할 수 있습니다.")
    return idx


def _problem_payload(row) -> dict:
    """뇌와 같은 문제 화면용 값. 정답(st_disease)은 숨긴다."""
    return {"idx": row.idx, "st_part": row.st_part, "st_modal": row.st_modal, "st_image": row.st_image}


# ── 무릎 학습 화면(GET /learn/knee)과 그 화면 전용 문제 제시(GET /learning/knee/task) ──────────────
# 화면 설계: 대표 영상 1장을 보고 ACL 참고 영역에 박스를 그린 뒤 위의 채점 API 로 비교한다(메인 화면의 AI 분석과는 별개).
# 이 두 주소는 무릎 전용이다. 공용 문제 API(/learning/problem)·로그인·관리자 화면은 건드리지 않는다.
_templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
_TRUE_VALUES = {"1", "true", "yes"}

# 영상 제공 주소·크기 공급자. 설계 4-1: image_url 은 서버가 인증·대상을 검증하는 제공 경로여야 하고, 폭·높이는 등록 때
# 계산해 둔 값이어야 한다. 그 저장소(manifest)와 영상 제공 경로는 팀장 작업(NAS·업로드)을 기다리므로 기본은 "연결 안 됨"(None)이다.
# 연결되면 provider(ref, manifest) -> {"image_url": str, "image_width": int, "image_height": int} 를 돌려주는 함수를 넣는다.
# 시험에서는 합성 영상으로 임시로 끼워 넣는다. 공급자가 없으면 승인된 대표가 있어도 문제를 내려주지 않는다(503).
_asset_provider = None


def set_asset_provider(provider) -> None:
    """영상 제공 주소·크기 공급자를 등록한다(None 이면 해제). 운영 연결 전에는 호출하지 않는다."""
    global _asset_provider
    _asset_provider = provider


# 제출 때 영상 검증을 **시험이 명시로** 바꾸는 자리. 운영 코드는 이것을 설정하지 않는다(검증 생략 허가가 아니다).
# 설정된 영상 공급원이 있으면 그 실제 검증이 항상 우선하고, 공급원이 없을 때만 이 값이 쓰인다. 둘 다 없으면 제출은 닫힌다.
_asset_verifier_override = None


def set_asset_verifier(verifier) -> None:
    """시험 전용: 영상 공급원이 없을 때 쓸 검증 함수(`(reference, manifest) -> None | 사유`)를 명시로 주입한다(None 이면 해제)."""
    global _asset_verifier_override
    _asset_verifier_override = verifier


def _image_source():
    """설정된 영상 공급원. NAS(`KNEE_NAS_IMAGE_BASE_URL`) 또는 서버 폴더(`KNEE_IMAGE_DIR`) 중 **정확히 하나만** 설정돼 있어야 한다.

    먼저 **원시 설정의 개수**로 판단한다: 둘 다 비었거나 둘 다 있으면 None(닫힘). 정확히 하나가 설정됐는데 그 값이 올바르지 않으면
    그 공급원만 시도해 None(닫힘)이 되며, **다른 공급원으로 조용히 넘어가지 않는다.** 어느 쪽이든 읽은 바이트의 SHA-256 과
    PNG 치수를 승인 기준과 대조한 것만 내보낸다.
    """
    config = import_module("class.knee.config")
    # 공급원 생성(local_assets/nas_assets)과 같은 실효값을 쓴다: 환경변수에 키가 있으면 빈 값도 명시적 비활성(FIVE-06)
    nas_raw = config.source_setting("KNEE_NAS_IMAGE_BASE_URL")
    folder_raw = config.source_setting("KNEE_IMAGE_DIR")
    if bool(nas_raw) == bool(folder_raw):
        return None
    module = import_module("class.knee.nas_assets" if nas_raw else "class.knee.local_assets")
    return module.source_from_settings()


def _default_provider():
    """영상 공급원이 설정돼 있으면 공급자, 없으면 None(닫힘).

    공급자는 영상을 가져와(NAS 또는 서버 폴더) 승인된 해시·치수와 대조한 뒤 폭·높이를 알려 주고, 영상 주소는 이 서버의
    `GET /learning/knee/image/{study_idx}/{presentation_id}` 로 돌려준다(NAS 주소·내부 경로를 학습자에게 보이지 않는다).
    """
    nas = import_module("class.knee.nas_assets")
    source = _image_source()
    key = import_module("class.knee.config").problem_token_key()
    if source is None or key is None:
        return None

    def provider(reference, manifest):
        image = source.get(reference.image_path, reference.image_sha256)
        if not nas.matches_approved_size(reference, image):
            raise nas.AssetMismatch("size differs from the approval")   # 승인 치수와 다르면 제시하지 않는다
        pid = slice_grade.presentation_id(key, study_idx=reference.study_idx, manifest=manifest, reference=reference)
        return {"image_url": f"/learning/knee/image/{reference.study_idx}/{pid}", "image_width": image.width, "image_height": image.height}

    return provider


def _default_verifier():
    """제출 때 대표 영상을 한 번 더 검증하는 함수(공급원이 설정돼 있을 때). 없으면 None = "자산 확인을 하지 않음"(확인했다는 뜻이 아니다).

    `grade_slide` 의 asset_verifier 로 연결된다. 내용이 승인된 해시·치수와 다르면 `asset_changed`, 가져오지 못하면
    `study_image_unavailable` 로 미채점·저장 0건이 된다.
    """
    nas = import_module("class.knee.nas_assets")
    source = _image_source()
    if source is None:
        return None

    def verifier(reference, manifest):
        try:
            image = source.get(reference.image_path, reference.image_sha256)
        except nas.AssetMismatch:
            return "asset_changed"
        except nas.AssetUnavailable:
            return "study_image_unavailable"
        return None if nas.matches_approved_size(reference, image) else "asset_changed"

    return verifier


def _series_conflicts_reference(study_idx: int, manifest, reference) -> bool:
    """이 문제에 열람 시리즈가 등록돼 있고, 그 대표 장이 채점 기준과 같은 영상이 아니면 True(채점을 닫는다). 등록이 없으면 False(한 장 모드)."""
    series_mod = import_module("class.knee.series_assets")
    resolution = series_mod.resolve(study_idx, manifest)
    if resolution.status == "none":
        return False
    if resolution.status == "ambiguous":
        return True   # 열람 등록이 둘 이상이라 어느 영상을 보고 있는지 알 수 없다
    return series_mod.conflicts_with_reference(resolution.assets, reference)


def _unconfigured_verifier(reference, manifest):
    """영상 공급원도 시험의 명시 주입도 없을 때의 검증기: 항상 "영상을 확인할 수 없음". 검증을 건너뛰고 채점하는 일이 없게 닫는다."""
    return "study_image_unavailable"


def _grade_verifier():
    """제출에 쓸 검증기. (1) 정확히 하나의 올바른 공급원이 설정돼 있으면 그 검증, (2) 아니면 시험의 명시 주입, (3) 아니면 닫힘.

    어떤 경우에도 "검증 없음"은 없다. 운영에서는 (1) 이 아니면 유효한 토큰으로 제출해도 미채점·저장 0건이다.
    """
    base = _default_verifier() or _asset_verifier_override or _unconfigured_verifier

    def verifier(reference, manifest):
        # 제출 때도 열람 시리즈의 대표 장이 기준과 같은 영상인지 다시 확인한다(task 뒤에 등록이 바뀐 경우·오래된 토큰 재사용 포함)
        if _series_conflicts_reference(reference.study_idx, manifest, reference):
            return "series_reference_mismatch"
        return base(reference, manifest)

    return verifier


SNAPSHOT_CHANGED_DETAIL = "문제 내용이 바뀌었습니다. 다시 열어 주세요."


def _snapshot_still_current(db, study, manifest, reference) -> bool:
    """영상을 공급하는 동안 문제 목록·승인 기준이 그대로인가. DB 에서 문제를 다시 읽어 같은 manifest·같은 승인 기준인지 대조한다.

    다시 읽을 수 없거나 하나라도 다르면 False(닫힘). 이 확인과 토큰 발급 사이의 아주 짧은 틈은 DB 트랜잭션·버전 재확인으로
    닫아야 한다(DB 연결 단계의 과제, HANDOFF 참조).
    """
    manifest_mod, refs, config = (import_module(f"class.knee.{n}") for n in ("slice_manifest", "slide_references", "config"))
    try:
        db.refresh(study)
        if study.del_yn != "N" or study.st_part != ST_PART_KNEE or study.st_modal != ST_MODAL_MRI:
            return False
        current = manifest_mod.build_manifest(study.st_image, config.asset_roots())
    except Exception:
        return False
    if current.version != manifest.version or current.list_sha256 != manifest.list_sha256:
        return False
    resolution = refs.resolve(study.idx, current)
    return resolution.status == "ok" and resolution.reference == reference


def _series_still_current(db, study, manifest, assets) -> bool:
    """여러 장 열람 중에 문제 목록·열람 자산 등록이 그대로인가(`_snapshot_still_current` 의 시리즈판, 기준 ROI 는 보지 않는다).

    DB 에서 문제를 다시 읽어 같은 목록(manifest)·같은 장별 해시/치수 등록인지 대조한다. 다시 읽을 수 없거나 하나라도 다르면 False(닫힘).
    """
    manifest_mod, series_mod, config = (import_module(f"class.knee.{n}") for n in ("slice_manifest", "series_assets", "config"))
    try:
        db.refresh(study)
        if study.del_yn != "N" or study.st_part != ST_PART_KNEE or study.st_modal != ST_MODAL_MRI:
            return False
        current = manifest_mod.build_manifest(study.st_image, config.asset_roots())
    except Exception:
        return False
    if current.version != manifest.version or current.list_sha256 != manifest.list_sha256:
        return False
    resolution = series_mod.resolve(study.idx, current)
    return resolution.status == "ok" and resolution.assets == assets


def _series_id_for(study_idx: int, manifest) -> str | None:
    """이 목록에 등록된 열람 자산이 있으면 그 series_id, 없으면(또는 키가 없으면) None. 문제 제시(task)에 실어 보낸다."""
    series_mod, config = (import_module(f"class.knee.{n}") for n in ("series_assets", "config"))
    key = config.problem_token_key()
    resolution = series_mod.resolve(study_idx, manifest)
    if key is None or resolution.status != "ok":
        return None
    return series_mod.series_id(key, study_idx=study_idx, manifest=manifest, assets=resolution.assets)


@router.get("/learn/knee", response_class=HTMLResponse, include_in_schema=False)
def knee_learning_page(request: Request, demo: str | None = None, study_idx: int | None = None):
    """무릎 ROI 학습 화면. ?demo=1 이면 합성 영상과 시연용 응답으로만 동작한다(서버 채점 아님).

    실제 연결 화면은 study_idx 로 문제를 열고 GET /learning/knee/task, POST /learning/roi-grade/knee 를 사용한다.
    이 화면은 정답·기준 박스·계산 규칙을 갖지 않는다. 로그인 여부·문제 열기·채점은 모두 서버가 정한다.
    """
    is_demo = (demo or "").lower() in _TRUE_VALUES
    response = _templates.TemplateResponse(
        request, "knee_learning.html",
        {"mode": "demo" if is_demo else "live", "study_idx": study_idx if (study_idx is not None and study_idx > 0) else None},
    )
    return response


@router.get("/learning/knee/task")
def learning_knee_task(request: Request, study_idx: int, db: Session = Depends(get_db)):
    """화면이 대표 영상을 열 때 쓰는 문제 제시. 설계 4-1 의 `knee_task` 를 돌려준다(토큰이 있어 저장 금지).

    - 꺼짐 503, 비로그인 401, 이메일 미인증 403, 무릎 MRI 문제가 아니면 404(채점 API 와 같은 검사).
    - 승인된 대표가 없거나 모호하면 200 + grading_available=false 와 사유만 준다(토큰·영상 주소 없음).
    - 승인된 대표가 있어도 영상 공급자(`set_asset_provider`)가 연결되지 않았으면 503 이다. 내부 경로를 그대로 내려주지 않는다.
    - 영상 공급과 토큰 발급은 같은 snapshot(manifest, 승인 기준)으로 한다. 공급하는 동안 문제 목록·승인 기준이 바뀌면 409 로 닫는다(섞지 않는다).
    - `presentation_id` 는 화면이 "같은 영상인가"를 비교하는 불투명한 값이다(경로·해시·번호를 드러내지 않는다).
    - 정답 박스·진단·출처·검수자는 어떤 경우에도 포함하지 않는다(build_knee_task 가 보장).
    """
    if _setting("KNEE_ROI_GRADE_ENABLED", "0") != "1":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.")
    user_idx = _learner_idx(request, db)

    study = study_repositories.get_by_idx(db, study_idx)
    if study is None or study.del_yn != "N" or study.st_part != ST_PART_KNEE or study.st_modal != ST_MODAL_MRI:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="학습할 수 있는 문제가 아닙니다.")

    manifest_mod, refs, config = (import_module(f"class.knee.{n}") for n in ("slice_manifest", "slide_references", "config"))
    try:
        manifest = manifest_mod.build_manifest(study.st_image, config.asset_roots())
    except manifest_mod.SliceManifestError:
        manifest = None   # build_knee_task 가 study_image_unavailable 로 답한다(단일 이미지 문제는 이 화면의 대상이 아니다)

    if manifest is not None:
        resolution = refs.resolve(study.idx, manifest)
        if resolution.status == "ok":
            # 승인된 대표가 있다: 영상 공급과 토큰 발급을 **같은 snapshot(manifest, reference)** 으로 한다(FINDING-SCREEN-017-03).
            reference = resolution.reference
            # 열람 시리즈의 대표 장이 이 기준과 같은 영상이 아니면 채점 토큰을 내주지 않는다(영상 탐색은 series 주소로 계속 된다).
            if _series_conflicts_reference(study.idx, manifest, reference):
                _log.error("knee task closed: series representative differs from the reference study_idx=%s", study.idx)   # 해시·경로는 남기지 않는다
                return JSONResponse(content=slice_grade.build_unavailable_task(
                    study_idx=study.idx, reason="series_reference_mismatch", slice_count=manifest.count), headers={"Cache-Control": "no-store"})
            provider = _asset_provider or _default_provider()
            if provider is None:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.")
            try:
                provided = provider(reference, manifest)
                image_url, image_width, image_height = provided["image_url"], provided["image_width"], provided["image_height"]
            except Exception as exc:   # 공급자 오류의 원문·경로는 남기지 않는다
                _log.error("knee task asset provider failed error_type=%s", type(exc).__name__)
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.") from None
            # 공급하는 동안 문제 목록·승인 기준이 바뀌었으면 섞어서 내려주지 않고 고정 오류로 닫는다(새 기준으로 자동 전환하지 않는다).
            if not _snapshot_still_current(db, study, manifest, reference):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SNAPSHOT_CHANGED_DETAIL)
            try:
                payload = slice_grade.build_knee_task_for_snapshot(
                    manifest=manifest, reference=reference, user_idx=user_idx, study_idx=study.idx,
                    image_url=image_url, image_width=image_width, image_height=image_height, now=int(time.time()),
                    series_id=_series_id_for(study.idx, manifest),
                )
            except slice_grade.SlideRequestError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
            except ValueError:   # 공급자가 준 주소·크기가 규칙에 맞지 않음. 값은 노출하지 않는다.
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.") from None
            return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})

    # 승인된 대표가 없음·모호·목록 사용 불가: 사유만 알린다(토큰·영상 주소 없음)
    try:
        payload = slice_grade.build_knee_task(
            user_idx=user_idx, study_idx=study.idx, st_image=study.st_image,
            image_url=None, image_width=None, image_height=None, now=int(time.time()),
        )
    except slice_grade.SlideRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})


@router.get("/learning/knee/image/{study_idx}/{presentation}")
def learning_knee_image(request: Request, study_idx: int, presentation: str, db: Session = Depends(get_db)):
    """승인된 대표 영상을 로그인한 학습자에게만 보여 준다(NAS 에서 가져와 승인된 해시와 대조한 바이트).

    - 꺼짐 503, 비로그인 401, 무릎 MRI 문제가 아니거나 현재 승인된 대표가 없으면 404.
    - 주소의 presentation 값이 **현재** 승인된 대표·영상의 식별값(presentation_id)과 같아야 한다. 승인·대표·영상이 바뀌면 옛 주소는 404.
    - NAS 공급원이 설정되지 않았거나 영상을 못 가져오거나 해시가 다르면 503(원인·주소는 노출하지 않는다). 다운로드 파일 이름·링크를 주지 않는다.
    """
    if _setting("KNEE_ROI_GRADE_ENABLED", "0") != "1":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.")
    _learner_idx(request, db)

    study = study_repositories.get_by_idx(db, study_idx)
    if study is None or study.del_yn != "N" or study.st_part != ST_PART_KNEE or study.st_modal != ST_MODAL_MRI:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="영상을 찾을 수 없습니다.")
    manifest_mod, refs, config, nas = (import_module(f"class.knee.{n}") for n in ("slice_manifest", "slide_references", "config", "nas_assets"))
    try:
        manifest = manifest_mod.build_manifest(study.st_image, config.asset_roots())
    except manifest_mod.SliceManifestError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="영상을 찾을 수 없습니다.") from None
    resolution = refs.resolve(study.idx, manifest)
    key = config.problem_token_key()
    if resolution.status != "ok" or key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="영상을 찾을 수 없습니다.")
    expected = slice_grade.presentation_id(key, study_idx=study.idx, manifest=manifest, reference=resolution.reference)
    if not hmac.compare_digest(presentation.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="영상을 찾을 수 없습니다.")
    source = _image_source()
    if source is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.")
    try:
        image = source.get(resolution.reference.image_path, resolution.reference.image_sha256)
        if not nas.matches_approved_size(resolution.reference, image):
            raise nas.AssetMismatch("size differs from the approval")
    except nas.AssetMismatch:
        _log.error("knee image content differs from the approved hash study_idx=%s", study.idx)   # 경로·해시는 남기지 않는다
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.") from None
    except nas.AssetUnavailable:
        _log.error("knee image unavailable study_idx=%s", study.idx)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.") from None
    # 영상을 읽는 동안(느린 NAS·디스크) 승인·문제가 바뀌었을 수 있으므로, 내보내기 전에 **사용한 snapshot** 과 현재 DB 를 다시 대조한다.
    # 바뀌었으면 바이트를 주지 않고 409 로 닫는다(새 기준으로 자동 전환하지 않는다). 이 대조 뒤의 극히 짧은 틈은 DB 트랜잭션 단계의 과제다.
    if not _snapshot_still_current(db, study, manifest, resolution.reference):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SNAPSHOT_CHANGED_DETAIL)
    # 읽어서 검증한 바이트를 그대로 내보낸다. 브라우저·중간 캐시에 남기지 않는다(no-store). 파일 이름·다운로드 헤더는 주지 않는다.
    return Response(content=image.data, media_type="image/png", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline",
    })


# ── 여러 장 열람(뇌처럼 휠·슬라이더로 장을 넘겨 본다). 설계 MSN-20261015-002 6장 ─────────────────────────────────────
# 영상 "열람"과 "채점"은 별개다: 아래 두 주소는 채점 스위치(KNEE_ROI_GRADE_ENABLED)·기준 ROI 표와 무관하게, **열람 자산 등록부**
# (class/knee/series_assets.py, 기본 비어 있음)에 등록된 문제의 장만 보여 준다. 정답·기준 박스·경로·해시는 어떤 응답에도 넣지 않는다.
# 대표 장 1개 기준의 채점 규칙은 그대로다(다른 장은 POST /learning/roi-grade/knee 가 미채점·저장 0으로 답한다).
_random = secrets.SystemRandom()


def active_case_ids(db: Session) -> list[int]:
    """열람 자산 등록부에 등록(A)돼 있고 DB 에서 활성(del_yn=N)인 무릎 MRI 문제 번호들(오름차순).

    공용 문제 표(state)와 무관하게 무릎 전용 등록부가 기준이다. 케이스가 많아도 DB 는 한 번, 가벼운 칸(번호·상태·부위·영상 종류)만 읽는다
    (영상 목록 st_image 를 전부 받으면 케이스 수만큼 응답이 커진다). 목록이 등록과 맞는지는 고른 케이스만 `_case_opens` 로 확인한다.
    """
    series_mod = import_module("class.knee.series_assets")
    ids = sorted(idx for idx, rows in series_mod.APPROVED_SERIES_ASSETS.items() if any(r.status == "A" for r in rows))
    if not ids:
        return []
    rows = db.execute(
        select(StudyModel.idx, StudyModel.del_yn, StudyModel.st_part, StudyModel.st_modal).where(StudyModel.idx.in_(ids))
    ).all()
    return sorted(r.idx for r in rows if r.del_yn == "N" and r.st_part == ST_PART_KNEE and r.st_modal == ST_MODAL_MRI)


def _case_opens(db: Session, idx: int) -> bool:
    """이 케이스의 현재 목록(manifest)이 열람 등록과 맞아 실제로 열 수 있는가(GET /learning/knee/series 가 열람을 주는 조건과 같다)."""
    manifest_mod, config, series_mod = (import_module(f"class.knee.{n}") for n in ("slice_manifest", "config", "series_assets"))
    study = study_repositories.get_by_idx(db, idx)
    if study is None or study.del_yn != "N" or study.st_part != ST_PART_KNEE or study.st_modal != ST_MODAL_MRI:
        return False
    try:
        manifest = manifest_mod.build_manifest(study.st_image, config.asset_roots())
    except manifest_mod.SliceManifestError:
        return False
    return series_mod.resolve(idx, manifest).status == "ok"


# 무작위 케이스(MRI 한 묶음) 고르기. 랜덤 단위는 이미지 한 장이 아니라 등록된 케이스(study) 하나다.
@router.get("/learning/knee/random")
def learning_knee_random(request: Request, exclude: int | None = None, db: Session = Depends(get_db)):
    """등록된 실제 무릎 MRI 케이스 중 하나를 고른다(로그인한 학습자만).

    - exclude: 지금 보고 있는 케이스 번호. 다른 케이스가 하나라도 있으면 그것을 빼고 고른다(케이스가 하나뿐이면 같은 번호를 줄 수 있고 same_as_excluded=true).
    - 후보가 없으면 200 + study_idx=null, unavailable_reason="no_registered_cases".
    - 정답·경로·해시·후보 목록은 보내지 않는다(번호 하나와 후보 수만). case_count 는 등록·활성 케이스 수다.
    - 고른 케이스의 목록이 등록과 맞지 않으면(열 수 없으면) 그 케이스를 버리고 다른 후보를 고른다(열 수 없는 케이스를 주지 않는다).
    """
    _learner_idx(request, db)
    headers = {"Cache-Control": "no-store"}
    ids = active_case_ids(db)
    others = [i for i in ids if i != exclude]
    _random.shuffle(others)
    pick = next((i for i in others if _case_opens(db, i)), None)
    if pick is None and exclude in ids and _case_opens(db, exclude):
        pick = exclude   # 열 수 있는 케이스가 지금 케이스뿐이다
    if pick is None:
        return JSONResponse(content={"study_idx": None, "case_count": 0, "same_as_excluded": False, "unavailable_reason": "no_registered_cases"}, headers=headers)
    return JSONResponse(content={"study_idx": pick, "case_count": len(ids), "same_as_excluded": pick == exclude, "unavailable_reason": None}, headers=headers)


@router.get("/learning/knee/series")
def learning_knee_series(request: Request, study_idx: int, db: Session = Depends(get_db)):
    """문제의 등록된 여러 장 목록과 장별 인증 영상 주소.

    - 비로그인 401, 이메일 미인증 403, 무릎 MRI 문제가 아니면 404.
    - 목록을 쓸 수 없거나 열람 자산이 등록되지 않았으면 200 + series_available=false 와 사유만 준다(영상 주소 없음).
    - 등록돼 있는데 영상 공급원(NAS/서버 폴더)이나 서명 키가 준비되지 않았으면 503(원인은 알리지 않는다).
    - initial_slice_index: 채점이 켜져 있고 승인된 대표가 이 목록에 맞으면 그 장, 아니면 가운데 장. 대표 번호는 채점이 켜졌을 때만 알려 준다.
    """
    _learner_idx(request, db)
    study = study_repositories.get_by_idx(db, study_idx)
    if study is None or study.del_yn != "N" or study.st_part != ST_PART_KNEE or study.st_modal != ST_MODAL_MRI:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="학습할 수 있는 문제가 아닙니다.")
    manifest_mod, refs, config, series_mod = (import_module(f"class.knee.{n}") for n in ("slice_manifest", "slide_references", "config", "series_assets"))
    headers = {"Cache-Control": "no-store"}

    def unavailable(reason: str):
        return JSONResponse(content={"study_idx": study.idx, "series_available": False, "unavailable_reason": reason, "slice_count": None}, headers=headers)

    try:
        manifest = manifest_mod.build_manifest(study.st_image, config.asset_roots())
    except manifest_mod.SliceManifestError:
        return unavailable("study_image_unavailable")
    resolution = series_mod.resolve(study.idx, manifest)
    if resolution.status == "ambiguous":
        return unavailable("series_ambiguous")
    if resolution.status != "ok":
        return unavailable("series_not_registered")
    assets = resolution.assets
    key = config.problem_token_key()
    if key is None or _image_source() is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.")
    sid = series_mod.series_id(key, study_idx=study.idx, manifest=manifest, assets=assets)
    grading_enabled = _setting("KNEE_ROI_GRADE_ENABLED", "0") == "1"
    representative = None
    if grading_enabled:
        ref = refs.resolve(study.idx, manifest)
        if ref.status == "ok":
            representative = ref.reference.slice_no
    slices = [
        {"slice_index": i, "image_url": f"/learning/knee/series/{study.idx}/{sid}/image/{i}", "image_width": w, "image_height": h}
        for i, (w, h) in enumerate(assets.slice_sizes)
    ]
    return JSONResponse(content={
        "study_idx": study.idx, "series_available": True, "series_id": sid, "slice_count": manifest.count,
        "initial_slice_index": representative if representative is not None else manifest.count // 2,
        "representative_slice_index": representative, "grading_enabled": grading_enabled,
        "source_kind": assets.kind, "source_notice": assets.notice, "slices": slices,
    }, headers=headers)


@router.get("/learning/knee/series/{study_idx}/{series}/image/{slice_index}")
def learning_knee_series_image(request: Request, study_idx: int, series: str, slice_index: str, db: Session = Depends(get_db)):
    """등록된 시리즈의 한 장을 로그인한 학습자에게 보여 준다(서버가 가진 목록·장별 해시로만 파일을 고르고, 한 번 읽어 검증한 바이트를 그대로 준다).

    - 비로그인 401, 무릎 MRI 문제가 아니거나 등록이 없거나 series_id·장 번호가 현재 등록과 다르면 404(옛 주소는 열리지 않는다).
    - 영상 공급원이 없거나 읽지 못하거나 해시·치수가 등록과 다르면 503(원인·경로는 알리지 않는다).
    - 읽는 동안 목록·등록이 바뀌었으면 바이트를 주지 않고 409 로 닫는다(다시 열기 안내).
    """
    _learner_idx(request, db)
    return _serve_series_image(db, study_idx, series, slice_index)


def _serve_series_image(db: Session, study_idx: int, series: str, slice_index: str) -> Response:
    """등록 시리즈의 한 장을 검증해 그대로 준다(학습자·게스트 공용, **권한 확인은 부르는 쪽이 먼저 한다**).

    DB 활성 무릎 MRI·현재 목록·열람 등록 resolve ok·series_id·장 번호 범위 → 공급원에서 읽기 → 등록 해시·치수 대조 → 읽는 동안 바뀌었는지 재확인.
    """
    study = study_repositories.get_by_idx(db, study_idx)
    not_found = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="영상을 찾을 수 없습니다.")
    if study is None or study.del_yn != "N" or study.st_part != ST_PART_KNEE or study.st_modal != ST_MODAL_MRI:
        raise not_found
    manifest_mod, config, series_mod, nas = (import_module(f"class.knee.{n}") for n in ("slice_manifest", "config", "series_assets", "nas_assets"))
    try:
        manifest = manifest_mod.build_manifest(study.st_image, config.asset_roots())
    except manifest_mod.SliceManifestError:
        raise not_found from None
    resolution = series_mod.resolve(study.idx, manifest)
    key = config.problem_token_key()
    if resolution.status != "ok" or key is None:
        raise not_found
    assets = resolution.assets
    expected = series_mod.series_id(key, study_idx=study.idx, manifest=manifest, assets=assets)
    if not hmac.compare_digest(series.encode("utf-8"), expected.encode("utf-8")):
        raise not_found
    try:
        index = manifest_mod.parse_slice_index(slice_index, manifest.count)
    except manifest_mod.SliceIndexError:
        raise not_found from None
    source = _image_source()
    if source is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.")
    try:
        image = source.get(manifest.paths[index], assets.slice_sha256[index])
        if (image.width, image.height) != tuple(assets.slice_sizes[index]):
            raise nas.AssetMismatch("size differs from the registration")
    except nas.AssetMismatch:
        _log.error("knee series image content differs from the registration study_idx=%s slice=%s", study.idx, index)   # 경로·해시는 남기지 않는다
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.") from None
    except nas.AssetUnavailable:
        _log.error("knee series image unavailable study_idx=%s slice=%s", study.idx, index)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.") from None
    if not _series_still_current(db, study, manifest, assets):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SNAPSHOT_CHANGED_DETAIL)
    return Response(content=image.data, media_type="image/png", headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline",
    })


# ── 게스트 실제 영상 열람(MSN-20261016-002): 로그인 없이 탐색·박스 연습만, 채점·풀이 저장 없음 ─────────────────────────────
# 독립 쿠키(mediscan_knee_guest)·서버 메모리 상태(services/kneeGuestViewServices.py)로만 구분한다. learner/user 세션을 만들거나 바꾸지 않고,
# 게스트 쿠키로 기존 task/grade/공용 API 의 학습자 권한을 얻을 수 없다. 기본 꺼짐(KNEE_GUEST_VIEW_ENABLED=1 일 때만), 허용 Origin 은 정확히 일치.
# 이 아래 주소에는 채점(POST grade)·task·토큰·기준 박스가 없다.
def _guest_json(content: dict, status_code: int = 200, retry_after: int | None = None) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if retry_after:
        headers["Retry-After"] = str(int(retry_after))
    return JSONResponse(content=content, status_code=status_code, headers=headers)


def _guest_error(exc) -> JSONResponse:
    return _guest_json({"error": exc.code, "detail": exc.message}, exc.status, exc.retry_after)


_ASCII_DIGITS = re.compile(r"[0-9]{1,9}")


def _guest_int(value: str | None, *, required: bool) -> int | None:
    """게스트 주소의 정수 입력을 직접 검사한다. FastAPI 기본 검증(422)은 공용 처리기로 빠져 no-store 가 붙지 않으므로(FINDING-GUEST-002)
    여기서 거절하고 게스트 응답(no-store)으로 돌려준다."""
    guest = import_module("services.kneeGuestViewServices")
    if value is None or value == "":
        if required:
            raise guest.GuestError(422, "guest_bad_request", "요청 값이 올바르지 않아요.")
        return None
    text = value.strip()
    # ASCII 숫자 0-9 만 허용한다. str.isdigit() 는 '²'·'１'·'١' 같은 문자도 숫자로 보지만 int() 가 실패해 500 으로 빠졌다(FINDING-GUEST-002 잔여).
    if not _ASCII_DIGITS.fullmatch(text):
        raise guest.GuestError(422, "guest_bad_request", "요청 값이 올바르지 않아요.")
    return int(text)


def _guest_http_error(exc: HTTPException) -> JSONResponse:
    """공용 영상 helper 가 올린 HTTPException(404·409·503 등)을 게스트 응답(no-store, 본문 detail 유지)으로 바꾼다. 공용 처리기·학습자 응답은 그대로."""
    return _guest_json({"detail": exc.detail}, exc.status_code)


def _guest_check(request: Request, *, post: bool) -> None:
    """스위치·출처 확인. 꺼져 있으면 DB·파일을 건드리기 전에 503. 시작/종료(POST)는 허용 Origin 과 정확히 일치해야 한다."""
    guest = import_module("services.kneeGuestViewServices")
    config = import_module("class.knee.config")
    if not guest.enabled(config.get_setting):
        raise guest.GuestError(503, "guest_view_disabled", "게스트 실제 영상 열람이 꺼져 있어요.")
    origins = guest.allowed_origins(config.get_setting)
    if not origins:
        raise guest.GuestError(503, "guest_view_not_configured", "게스트 실제 영상 열람이 준비되지 않았어요.")
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise guest.GuestError(403, "guest_origin_forbidden", "허용되지 않은 곳에서 온 요청이에요.")
    if post and (not origin or origin.rstrip("/") not in origins):
        raise guest.GuestError(403, "guest_origin_forbidden", "허용되지 않은 곳에서 온 요청이에요.")
    if origin and origin.rstrip("/") not in origins:
        raise guest.GuestError(403, "guest_origin_forbidden", "허용되지 않은 곳에서 온 요청이에요.")


def _guest_state(request: Request):
    guest = import_module("services.kneeGuestViewServices")
    return guest.store().get(request.cookies.get(guest.COOKIE_NAME))


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"   # 직접 연결 상대만 본다(X-Forwarded-For 를 믿지 않는다)


def _guest_session_body(state) -> dict:
    guest = import_module("services.kneeGuestViewServices")
    return {"access_mode": "guest_view", "read_only": True, "grading_available": False, **guest.store().remaining(state)}


@router.post("/learning/knee/guest/session")
def learning_knee_guest_session(request: Request):
    """게스트 열람 시작. 유효한 게스트 쿠키가 있으면 그 상태(남은 시간·케이스 한도)를 그대로 돌려주고, 없으면 새로 발급한다(발급 제한 적용)."""
    guest = import_module("services.kneeGuestViewServices")
    config = import_module("class.knee.config")
    try:
        _guest_check(request, post=True)
        try:
            state = _guest_state(request)
            return _guest_json(_guest_session_body(state))
        except guest.GuestError as exc:
            if exc.status != 401:
                raise
        token, state = guest.store().issue(_client_ip(request))
    except guest.GuestError as exc:
        return _guest_error(exc)
    res = _guest_json(_guest_session_body(state))
    res.set_cookie(guest.COOKIE_NAME, token, max_age=guest.store().limits.session_ttl_s, path="/", httponly=True,
                   samesite="lax", secure=guest.cookie_secure(config.get_setting))
    return res


@router.post("/learning/knee/guest/session/end")
def learning_knee_guest_session_end(request: Request):
    """게스트 열람 종료: 이 게스트 상태와 독립 쿠키만 지운다(학습자·관리자 세션은 건드리지 않는다)."""
    guest = import_module("services.kneeGuestViewServices")
    config = import_module("class.knee.config")
    try:
        _guest_check(request, post=True)
    except guest.GuestError as exc:
        return _guest_error(exc)
    guest.store().end(request.cookies.get(guest.COOKIE_NAME))
    res = _guest_json({"ended": True})
    res.delete_cookie(guest.COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=guest.cookie_secure(config.get_setting))
    return res


@router.get("/learning/knee/guest/random")
def learning_knee_guest_random(request: Request, exclude: str | None = None, db: Session = Depends(get_db)):
    """등록된 케이스 중 하나를 골라 이 게스트 세션에 허용한다(랜덤 단위 = 케이스). 응답은 번호·후보 수·남은 케이스 수뿐."""
    guest = import_module("services.kneeGuestViewServices")
    try:
        _guest_check(request, post=False)
        exclude = _guest_int(exclude, required=False)
        state = _guest_state(request)
        store = guest.store()
        store.check_meta(state, _client_ip(request))
        ids = active_case_ids(db)
        others = [i for i in ids if i != exclude]
        _random.shuffle(others)
        # 새 케이스 한도를 다 썼으면 이미 허용된 케이스 중에서만 고른다(한도를 늘리지 않는다)
        if not store.can_grant_new(state):
            others = [i for i in others if store.granted_series(state, i) is not None]
            # 이미 본 다른 케이스가 없어도 지금 케이스가 허용돼 있으면 그 케이스를 다시 연다(same_as_excluded, 한도 차감 없음). 그것도 없으면 한도 안내.
            if not others and not (exclude in ids and store.granted_series(state, exclude) is not None):
                raise guest.GuestError(429, "guest_case_limit", "이번 게스트 열람에서 볼 수 있는 케이스 수를 다 썼어요.",
                                       store.remaining(state)["expires_in"] or 1)
        pick, sid = None, None
        for idx in others[: store.limits.random_attempts]:
            sid = _guest_open_series_id(db, idx)
            if sid is not None:
                pick = idx
                break
        if pick is None and exclude in ids:
            sid = _guest_open_series_id(db, exclude)
            pick = exclude if sid is not None else None
        if pick is None:
            return _guest_json({"study_idx": None, "case_count": 0, "same_as_excluded": False, "remaining_cases": store.remaining(state)["remaining_cases"],
                                "unavailable_reason": "no_registered_cases"})
        store.grant(state, pick, sid)
        return _guest_json({"study_idx": pick, "case_count": len(ids), "same_as_excluded": pick == exclude,
                            "remaining_cases": store.remaining(state)["remaining_cases"], "unavailable_reason": None})
    except guest.GuestError as exc:
        return _guest_error(exc)


def _guest_open_series_id(db: Session, idx: int) -> str | None:
    """이 케이스가 지금 열람 가능하면(활성 무릎 MRI·목록·등록 resolve ok·키 있음) 그 series_id, 아니면 None."""
    if not _case_opens(db, idx):
        return None
    study = study_repositories.get_by_idx(db, idx)
    manifest_mod, config = (import_module(f"class.knee.{n}") for n in ("slice_manifest", "config"))
    try:
        manifest = manifest_mod.build_manifest(study.st_image, config.asset_roots())
    except manifest_mod.SliceManifestError:
        return None
    return _series_id_for(idx, manifest)


@router.get("/learning/knee/guest/series")
def learning_knee_guest_series(request: Request, study_idx: str | None = None, db: Session = Depends(get_db)):
    """이 게스트 세션에 이미 허용된 케이스의 장 목록(게스트 영상 주소). 기준 장·정답·토큰·경로·해시는 없다. 허용되지 않은 케이스는 403."""
    guest = import_module("services.kneeGuestViewServices")
    try:
        _guest_check(request, post=False)
        study_idx = _guest_int(study_idx, required=True)
        state = _guest_state(request)
        store = guest.store()
        store.check_meta(state, _client_ip(request))
        if store.granted_series(state, study_idx) is None:
            raise guest.GuestError(403, "guest_case_not_granted", "이 케이스는 지금 열람할 수 없어요. 무작위로 다시 받아 주세요.")
    except guest.GuestError as exc:
        return _guest_error(exc)
    study = study_repositories.get_by_idx(db, study_idx)
    if study is None or study.del_yn != "N" or study.st_part != ST_PART_KNEE or study.st_modal != ST_MODAL_MRI:
        return _guest_json({"error": "guest_case_unavailable", "detail": "열람할 수 있는 케이스가 아니에요."}, 404)
    manifest_mod, config, series_mod = (import_module(f"class.knee.{n}") for n in ("slice_manifest", "config", "series_assets"))
    try:
        manifest = manifest_mod.build_manifest(study.st_image, config.asset_roots())
    except manifest_mod.SliceManifestError:
        return _guest_json({"study_idx": study.idx, "series_available": False, "unavailable_reason": "study_image_unavailable", "slice_count": None})
    resolution = series_mod.resolve(study.idx, manifest)
    if resolution.status != "ok":
        reason = "series_ambiguous" if resolution.status == "ambiguous" else "series_not_registered"
        return _guest_json({"study_idx": study.idx, "series_available": False, "unavailable_reason": reason, "slice_count": None})
    assets = resolution.assets
    key = config.problem_token_key()
    if key is None or _image_source() is None:
        return _guest_json({"error": "guest_source_unavailable", "detail": "지금은 영상을 불러올 수 없어요."}, 503)
    sid = series_mod.series_id(key, study_idx=study.idx, manifest=manifest, assets=assets)
    store.grant(state, study.idx, sid)   # 명시적 다시 열기: 현재 유효성을 확인한 series_id 로 갱신(이미 허용된 케이스라 한도 차감 없음)
    slices = [
        {"slice_index": i, "image_url": f"/learning/knee/guest/series/{study.idx}/{sid}/image/{i}", "image_width": w, "image_height": h}
        for i, (w, h) in enumerate(assets.slice_sizes)
    ]
    return _guest_json({
        "study_idx": study.idx, "series_available": True, "series_id": sid, "slice_count": manifest.count,
        "initial_slice_index": manifest.count // 2, "representative_slice_index": None, "grading_enabled": False,
        "read_only": True, "grading_available": False, "access_mode": "guest_view",
        "source_kind": assets.kind, "source_notice": assets.notice, "slices": slices,
    })


@router.get("/learning/knee/guest/series/{study_idx}/{series}/image/{slice_index}")
def learning_knee_guest_series_image(request: Request, study_idx: str, series: str, slice_index: str, db: Session = Depends(get_db)):
    """이 게스트 세션에 허용된 케이스·시리즈의 한 장. 허용 안 됨 403, 허용 때와 시리즈가 달라짐 409(다시 열기), 그 밖은 학습자와 같은 검증."""
    guest = import_module("services.kneeGuestViewServices")
    try:
        _guest_check(request, post=False)
        study_idx = _guest_int(study_idx, required=True)
        state = _guest_state(request)
        store = guest.store()
        store.check_image(state, _client_ip(request))
        granted = store.granted_series(state, study_idx)
        if granted is None:
            raise guest.GuestError(403, "guest_case_not_granted", "이 케이스는 지금 열람할 수 없어요.")
        if not hmac.compare_digest(series.encode("utf-8"), granted.encode("utf-8")):
            raise guest.GuestError(409, "guest_series_changed", "영상이 바뀌었어요. 다시 열어 주세요.")
    except guest.GuestError as exc:
        return _guest_error(exc)
    try:
        return _serve_series_image(db, study_idx, series, slice_index)
    except HTTPException as exc:
        return _guest_http_error(exc)


@router.post("/learning/roi-grade/knee")
async def learning_roi_grade_knee(
    request: Request,
    db: Session = Depends(get_db),
    study_idx: int = Form(..., description="현재 문제 study.idx (무릎 MRI 문제)"),
    roi_type: str = Form(..., description="box (박스만 사용할 수 있다)"),
    x: float | None = Form(None, description="박스 왼쪽 x (이미지 전체 0~1)"),
    y: float | None = Form(None, description="박스 위쪽 y (0~1)"),
    width: float | None = Form(None, description="박스 너비 (0~1)"),
    height: float | None = Form(None, description="박스 높이 (0~1)"),
    slice_index: str | None = Form(None, description="슬라이드 문제에서 화면이 보여 준 슬라이스의 등록 배열 위치(0부터). 슬라이드 문제에서만 필수"),
    problem_token: str | None = Form(None, description="문제를 열 때 서버가 준 problem_token. 슬라이드 문제에서 승인된 대표가 있을 때 필수"),
):
    if _setting("KNEE_ROI_GRADE_ENABLED", "0") != "1":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="이 기능은 지금 사용할 수 없습니다.")
    user_idx = _learner_idx(request, db)

    study = study_repositories.get_by_idx(db, study_idx)
    if study is None or study.del_yn != "N" or study.st_part != ST_PART_KNEE or study.st_modal != ST_MODAL_MRI:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="채점할 수 있는 문제가 아닙니다.")

    try:
        user = knee_grade.user_mask(roi_type=roi_type, x=x, y=y, width=width, height=height)
    except ValueError as exc:  # 박스가 아니거나 잘못된 ROI
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    # 입력 검사를 먼저 하고, 그다음 문제 이미지의 형태를 본다. 확인 불가는 채점하지 않는다.
    image_path, not_graded_reason = knee_grade.single_image_path(study.st_image)
    if not_graded_reason == "slide_not_supported":
        # 슬라이드(여러 장) 문제: 서버가 정한 대표 슬라이스 1장만 채점한다(설계 REPRESENTATIVE_SLICE_CONTRACT). 판단은 서비스에서 한다.
        try:
            # 제출 때도 대표 영상을 승인된 해시·치수와 대조한다(asset_verifier). 검증을 건너뛰는 경로는 없다: 정확히 하나의 올바른 공급원이
            # 설정돼 있으면 그 검증, 아니면 시험의 명시 주입, 아니면 항상 "확인할 수 없음"(미채점·저장 0). 느릴 수 있어 스레드풀에서 돈다.
            outcome = await run_in_threadpool(
                slice_grade.grade_slide,
                user_mask=user, user_idx=user_idx, study_idx=study.idx, st_image=study.st_image,
                slice_index_raw=slice_index, problem_token=problem_token, now=int(time.time()),
                asset_verifier=_grade_verifier(),
            )
        except slice_grade.SlideRequestError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
        # 영상 검증(네트워크·디스크 대기) 동안 승인·문제가 바뀌었을 수 있다. 저장 전에 **채점에 쓴 snapshot** 과 현재 DB 를 다시 대조하고,
        # 바뀌었으면 채점 결과를 버리고 미채점(presentation_changed)·저장 0 으로 닫는다. 새 기준으로 자동 갈아타지 않는다.
        if outcome.image_path is not None and outcome.snapshot is not None \
                and not _snapshot_still_current(db, study, outcome.snapshot[0], outcome.snapshot[1]):
            outcome = slice_grade.SlideOutcome(knee_grade.not_graded(user, "presentation_changed"), None)
        result, image_path = outcome.result, outcome.image_path
    elif image_path is None:
        result = knee_grade.not_graded(user, not_graded_reason)
    else:
        reference = import_module("class.knee.reference_rois").find_reference(study.idx)
        result = knee_grade.grade(user, reference)

    if result["graded"]:
        try:
            row = review_node_repositories.create(
                db,
                rn_u_idx=user_idx,
                rn_s_idx=study.idx,
                rn_disease=(study.st_disease or "").strip()[:30] or None,
                rn_image=image_path,  # 서버가 가진 문제의 단일 이미지 경로. 사용자가 올린 이미지·배열 문자열은 없다.
                rn_solution=review_node_service.solution_code(result["result"]),
                state="N",
            )
        except Exception as exc:
            _log.error("knee roi-grade save failed error_type=%s", type(exc).__name__)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="리뷰 저장 실패") from None
        result.update(review_idx=row.idx, rn_image=row.rn_image, rn_solution=row.rn_solution)
        await review_events.publish(
            "review_created",
            {"review_idx": row.idx, "user_idx": user_idx, "study_idx": study.idx,
             "rn_image": row.rn_image, "rn_solution": row.rn_solution},
        )

    try:
        next_row = learning_service.get_random_problem(
            db, st_part=ST_PART_KNEE, st_modal=ST_MODAL_MRI, exclude_idxs=[study.idx], user_idx=user_idx)
    except ValueError:
        next_row = None
    result["next"] = _problem_payload(next_row) if next_row else None
    return result
