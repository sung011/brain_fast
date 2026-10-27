"""
무릎 MRI 화면 캡처 한 장 분석 API 라우터.

main.py 에서 prefix="/api/v1" 을 붙인다. 실제 주소: POST /api/v1/knee/screen-roi
뇌 CT(routers/brain.py)와 같은 위치에 있지만 요청·응답 형식은 공유하지 않는다(무릎은 부위·촬영면·이미지 범위를 함께 받는다).
설계: setting/designs/MSN-20260930-001/DESIGN.md

하는 일:
- multipart 로 image(PNG/JPEG 한 장), body_part="knee", plane, image_scope, include_overlay(선택)를 받는다.
- 필드 누락·중복·모르는 값은 400, 크기 초과 413, PNG/JPEG 가 아니면 415, 읽을 수 없으면 422.
- 계산은 class/knee 에 맡긴다(라우터는 얇게). 그 촬영면에 모델이 있으면 점수를 낸다.
- 응답을 보내기 전에 업로드 파일을 닫고 지운다. 이미지 파일·파일 이름·예외 원문은 로그와 저장소에 남기지 않는다.
- 판독 문구(소견·점수·판정)는 review_node 에 남긴다. KNEE_SCREEN_SAVE_ENABLED=0 이면 저장만 건너뛴다.

하지 않는 일: 로그인 여부 검사, 이미지 파일 보관.
KNEE_SCREEN_ROI_ENABLED=0 이면 이 주소를 닫는다(기본은 열림. 실제 사용자 공개 전에는 인증·로그 정책을 먼저 해결한다).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from importlib import import_module

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from schemas.kneeSchemas import KneeErrorBody, KneeScreenRoiResponse

router = APIRouter()
_log = logging.getLogger("knee_api")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_ALLOWED_FIELDS = {"image", "body_part", "plane", "image_scope", "include_overlay"}
_REQUIRED_FIELDS = ("image", "body_part", "plane", "image_scope")
_PLANES = ("axial", "coronal", "sagittal")
_SCOPES = ("full_slice", "roi_crop")


class _ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(code)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def _error_response(request_id: str, err: _ApiError) -> JSONResponse:
    body = KneeErrorBody(request_id=request_id, code=err.code, message=err.message, retryable=err.retryable)
    return JSONResponse(status_code=err.status, content=body.model_dump())


def _setting(name: str, default: str) -> str:
    return import_module("class.knee.config").get_setting(name, default)


def _optional_user_idx(request: Request) -> int | None:
    """기록에 넣을 회원 번호. 세션이 없어도 분석을 막지 않는다."""
    raw = request.scope.get("session")
    raw = raw if isinstance(raw, dict) else {}
    learner = raw.get("learner") if isinstance(raw.get("learner"), dict) else None
    admin = raw.get("user") if isinstance(raw.get("user"), dict) else None
    for source in (learner, admin):
        if source is None:
            continue
        value = source.get("idx")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            continue
        return value
    return None


def _save_reading(request: Request, result: dict) -> int | None:
    if _setting("KNEE_SCREEN_SAVE_ENABLED", "1") != "1":
        return None
    service = import_module("services.kneeScreenServices")
    try:
        return service.save_screen_reading(user_idx=_optional_user_idx(request), result=result)
    except Exception as exc:
        _log.error("knee screen reading save failed error_type=%s", type(exc).__name__)
        return None


def _read_fields(form) -> tuple[object, str, str, bool]:
    """폼에서 필드를 꺼내 검사한다. 누락·중복·모르는 필드·모르는 값은 400."""
    items = form.multi_items()
    counts: dict[str, int] = {}
    for key, _value in items:
        counts[key] = counts.get(key, 0) + 1
    if any(key not in _ALLOWED_FIELDS for key in counts):
        raise _ApiError(400, "unknown_field", "허용되지 않은 항목이 있습니다.")
    if any(count > 1 for count in counts.values()):
        raise _ApiError(400, "duplicate_field", "같은 항목이 두 번 이상 들어 있습니다.")
    for key in _REQUIRED_FIELDS:
        if key not in counts:
            raise _ApiError(400, "missing_field", "필수 항목이 빠져 있습니다.")

    upload = form.get("image")
    if not hasattr(upload, "read"):
        raise _ApiError(400, "image_not_file", "image 항목은 파일이어야 합니다.")
    body_part, plane, scope = form.get("body_part"), form.get("plane"), form.get("image_scope")
    if not all(isinstance(v, str) for v in (body_part, plane, scope)):
        raise _ApiError(400, "invalid_field", "항목 값이 올바르지 않습니다.")
    if body_part != "knee":
        raise _ApiError(400, "invalid_body_part", "body_part 는 'knee' 여야 합니다.")
    if plane not in _PLANES:
        raise _ApiError(400, "invalid_plane", "plane 은 axial, coronal, sagittal 중 하나여야 합니다.")
    if scope not in _SCOPES:
        raise _ApiError(400, "invalid_image_scope", "image_scope 는 full_slice 또는 roi_crop 이어야 합니다.")

    include_overlay = True
    if "include_overlay" in counts:
        raw = form.get("include_overlay")
        if not isinstance(raw, str) or raw.strip().lower() not in ("true", "false"):
            raise _ApiError(400, "invalid_boolean", "include_overlay 는 true 또는 false 여야 합니다.")
        include_overlay = raw.strip().lower() == "true"
    return upload, plane, scope, include_overlay


async def _close_all(form, upload) -> bool:
    """업로드 파일과 폼(임시 파일 포함)을 닫는다. 하나라도 실패하면 False."""
    ok = True
    for closer in (getattr(upload, "close", None), getattr(form, "close", None)):
        if closer is None:
            continue
        try:
            result = closer()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            ok = False
    return ok


@router.post(
    "/knee/screen-roi",
    summary="무릎 MRI 화면 캡처 한 장 분석",
    responses={
        200: {"model": KneeScreenRoiResponse},
        400: {"model": KneeErrorBody}, 413: {"model": KneeErrorBody}, 415: {"model": KneeErrorBody},
        422: {"model": KneeErrorBody}, 500: {"model": KneeErrorBody}, 503: {"model": KneeErrorBody},
        504: {"model": KneeErrorBody},
    },
)
async def knee_screen_roi(request: Request):
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    form = None
    upload = None
    try:
        if _setting("KNEE_SCREEN_ROI_ENABLED", "1") != "1":
            raise _ApiError(503, "feature_disabled", "이 기능은 지금 사용할 수 없습니다.", retryable=False)

        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES + 64 * 1024:
            raise _ApiError(413, "payload_too_large", "올린 파일이 너무 큽니다(최대 5MiB).")
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            raise _ApiError(400, "invalid_form", "multipart/form-data 형식으로 보내 주세요.")

        try:
            form = await request.form(max_files=2, max_fields=8)  # 파일 2개까지 읽어 image 중복을 알려 준다(전체 크기는 위에서 제한)
        except Exception:
            raise _ApiError(400, "invalid_form", "요청 형식을 읽을 수 없습니다.") from None

        upload, plane, scope, include_overlay = _read_fields(form)

        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise _ApiError(413, "payload_too_large", "올린 파일이 너무 큽니다(최대 5MiB).")

        imageio = import_module("class.knee.imageio")
        try:
            decoded = imageio.decode_screen_image(data)
        except imageio.ImageRejected as rejected:
            raise _ApiError(rejected.status, rejected.code, rejected.message) from None
        finally:
            data = None  # 바이트 버퍼 참조를 놓는다

        screen_roi = import_module("class.knee.screen_roi")
        errors = import_module("class.knee.errors")
        timeout = float(_setting("KNEE_SCREEN_ROI_TIMEOUT_S", "30"))
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    screen_roi.analyze, decoded.gray, plane=plane, image_scope=scope, include_overlay=include_overlay,
                    width=decoded.width, height=decoded.height, request_id=request_id,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise _ApiError(504, "timeout", "분석 시간이 초과되었습니다. 다시 시도해 주세요.", retryable=True) from None
        except errors.KneeModelNotReady:
            raise _ApiError(503, "model_unavailable", "분석 모델을 지금 준비하지 못했습니다. 잠시 뒤 다시 시도해 주세요.", retryable=True) from None
        except errors.KneeInputError:
            raise _ApiError(422, "invalid_image", "이미지를 분석할 수 없습니다.") from None
        finally:
            decoded = None

        result["review_idx"] = _save_reading(request, result)

        # 응답을 보내기 전에 업로드를 닫고 지운다. 실패하면 성공으로 답하지 않는다.
        if not await _close_all(form, upload):
            raise _ApiError(500, "cleanup_failed", "서버 처리 중 문제가 생겼습니다.")
        form = upload = None

        result["retention"] = {"input_deleted": True, "transient_only": True, "scope_note": screen_roi.RETENTION_NOTE}
        return JSONResponse(content=KneeScreenRoiResponse.model_validate(result).model_dump())

    except _ApiError as err:
        return _error_response(request_id, err)
    except Exception as exc:  # 예상 못 한 오류: 내용은 남기지 않고 종류만 기록한다.
        _log.error("knee screen-roi failed request_id=%s error_type=%s", request_id, type(exc).__name__)
        return _error_response(request_id, _ApiError(500, "internal_error", "서버 처리 중 문제가 생겼습니다."))
    finally:
        if form is not None or upload is not None:
            await _close_all(form, upload)
