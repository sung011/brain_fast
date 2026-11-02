"""
무릎 MRI 화면 분석(넘기며 분석 / 한 장) API — MSN-20261021-001 v2.

main.py 에서 prefix="/api/v1". 실제 주소: POST /api/v1/knee/screen-analysis
기존 뇌(/api/v1/analyze/screen-roi·/explain)와 기존 무릎(/api/v1/knee/screen-roi) 주소·응답은 그대로 둔다.

multipart/form-data
- frames: PNG/JPEG 파일 반복(한 장 1 또는 넘기며 10~20)
- manifest: JSON 문자열(schema_version·plane·image_scope·capture_intent·capture_profile_id·frames[{sequence,file_index,captured_at_ms}])
- include_overlay: "true"/"false"(선택, 기본 true)
- scroll_score_mode: "legacy"/"ensemble"(선택, 기본 legacy = 기존 B). MSN-20261102-004.
  ensemble 은 실행 프로세스 KNEE_SCROLL_ENSEMBLE_ENABLED=1 일 때만(아니면 503 ensemble_disabled), 넘기며 분석 + 영상 한 장 전체(full_slice)만
  (한 장·roi_crop 은 422 unsupported_ensemble_input). 고유 10~14장은 200 not_evaluated(앙상블은 15장 필요). B 점수로 대신하지 않는다.
그 밖의 필드(모델 이름·점수·승인 등)는 400 으로 거절한다.

검증·선택·응답은 class/knee/scroll_analysis.py. 모델: 넘기며 분석 = class/knee/acl_volume.py(ACL 시상면 볼륨 하나만 lazy load),
한 장 = class/knee/single_image.py(사용자 Colab 반입 2D 모델, 서버 설정 KNEE_SINGLE_BUNDLE_DIR·고정 SHA). 두 모델은 같은 실행권(_BUSY)을 쓴다.
넘기며 분석의 그림은 여러 장 모델 히트맵이 아니라 별도 ACL 위치 모델(class/knee/screen_localization.py, 사용 장의 가운데 한 장)이다.
업로드·디코드 결과는 이 요청 동안만 메모리에 두고 응답 전에 닫는다. 로그·DB·NAS 에 이미지·파일 이름을 남기지 않는다. 학습 기록(review_node)을 쓰지 않는다.

자원 보호(Codex STEP2-001·002)
- 요청 본문은 multipart 파싱 **전에** 실제로 받은 바이트로 센다(Content-Length 유무·거짓 값과 무관). 한도(16MiB + 256KiB 머리말 여유)를
  넘는 순간 나머지를 더 읽지 않고 413. 파서 임시 파일에는 한도 안의 본문만 들어간다.
- 계산 실행권(_BUSY)은 **계산 스레드가 실제로 끝날 때** 스레드 안에서 한 번만 놓는다. 504(시간 초과)로 응답한 뒤에도 그 계산이 끝날 때까지
  다음 요청은 429 를 받는다(재시도로 계산이 쌓이지 않게). 계산 스레드는 강제로 멈출 수 없다.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from importlib import import_module

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
_log = logging.getLogger("knee_screen_analysis")
_ALLOWED = {"frames", "manifest", "include_overlay", "scroll_score_mode"}
BODY_OVERHEAD = 256 * 1024
_SINGLE_NOT_READY = "한 장 분석 모델을 지금 사용할 수 없습니다(서버 준비 필요). MRI 를 넘기며 '넘기며 분석'을 사용해 주세요."
_SCORE_MODES = ("legacy", "ensemble")
_ENSEMBLE_DISABLED = "이 서버에서는 앙상블(B+v4) 점수를 켜지 않았습니다. 점수 모델을 '기존 B'로 바꿔 다시 분석해 주세요."
_ENSEMBLE_UNAVAILABLE = "앙상블(B+v4) 모델을 지금 준비하지 못했습니다. 점수 모델을 '기존 B'로 바꿔 다시 분석할 수 있습니다."


class _Slot:
    """계산 실행권 하나. 비차단 획득, 계산 스레드가 끝날 때 정확히 한 번 해제."""

    def __init__(self) -> None:
        self._sem = threading.Semaphore(1)
        self._held = False
        self._guard = threading.Lock()

    def try_acquire(self) -> bool:
        if not self._sem.acquire(blocking=False):
            return False
        with self._guard:
            self._held = True
        return True

    def release(self) -> None:
        with self._guard:
            if not self._held:
                return
            self._held = False
        self._sem.release()

    def locked(self) -> bool:
        with self._guard:
            return self._held


_BUSY = _Slot()


def _err(request_id, status, code, message, retryable=False, score_mode=None):
    body = {"request_id": request_id, "code": code, "message": message, "retryable": retryable}
    if score_mode is not None:   # 모드를 읽은 뒤의 오류에는 어느 모드 요청이었는지 함께 준다(점수는 없음)
        body["score_mode"] = score_mode
    return JSONResponse(status_code=status, content=body)


def _setting(name, default):
    return import_module("class.knee.config").get_setting(name, default)


async def _read_limited(request: Request, limit: int):
    """본문을 limit 바이트까지만 받는다. 넘으면 None(나머지는 읽지 않음)."""
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/knee/screen-analysis/config", summary="넘기며 분석 점수 모드 사용 가능 여부(화면 기본값용)")
async def knee_screen_analysis_config():
    """화면이 기본 점수 모드(앙상블)를 정할 때 쓴다. 모델을 읽지 않고 설정·manifest 만 본다. 경로·지문·비밀값은 돌려주지 않는다."""
    ens = import_module("class.knee.scroll_ensemble")
    ready = False
    if ens.enabled():
        try:
            ens.check_bundle_config()
            ready = True
        except ens.EnsembleUnavailable:
            ready = False
    return JSONResponse(content={"ensemble_enabled": ens.enabled(), "ensemble_ready": ready,
                                 "default_scroll_score_mode": "ensemble" if ready else "legacy"})


@router.post("/knee/screen-analysis", summary="무릎 MRI 화면 분석(넘기며 분석 10~20장 / 한 장)")
async def knee_screen_analysis(request: Request):
    sa = import_module("class.knee.scroll_analysis")
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    started = time.perf_counter()
    form = None
    mode = None
    try:
        if _setting("KNEE_SCREEN_ANALYSIS_ENABLED", "1") != "1":
            return _err(request_id, 503, "feature_disabled", "이 기능은 지금 사용할 수 없습니다.")
        limit = sa.MAX_TOTAL_BYTES + BODY_OVERHEAD
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > limit:
            return _err(request_id, 413, "payload_too_large", "올린 파일이 너무 큽니다(전체 16MiB).")
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            return _err(request_id, 400, "invalid_form", "multipart/form-data 형식으로 보내 주세요.")
        body = await _read_limited(request, limit)
        if body is None:
            return _err(request_id, 413, "payload_too_large", "올린 파일이 너무 큽니다(전체 16MiB).")

        async def receive():   # 한도 안에서 받은 본문만 파서에 준다
            return {"type": "http.request", "body": body, "more_body": False}

        try:
            form = await Request(request.scope, receive).form(max_files=sa.MAX_FRAMES + 1, max_fields=4)
        except Exception:
            return _err(request_id, 400, "invalid_form", "요청 형식을 읽을 수 없습니다(파일 수 초과 포함).")
        body = None
        items = form.multi_items()
        keys = [k for k, _ in items]
        if any(k not in _ALLOWED for k in keys) or keys.count("manifest") != 1 or keys.count("include_overlay") > 1:
            return _err(request_id, 400, "unknown_field", "허용되지 않은 항목이 있거나 manifest 가 없습니다.")
        if keys.count("scroll_score_mode") > 1:
            return _err(request_id, 400, "invalid_score_mode", "scroll_score_mode 는 한 번만 보낼 수 있습니다.")
        mode = "legacy"
        if "scroll_score_mode" in keys:
            raw_mode = form.get("scroll_score_mode")
            if not isinstance(raw_mode, str) or raw_mode not in _SCORE_MODES:
                mode = None
                return _err(request_id, 400, "invalid_score_mode", "scroll_score_mode 는 legacy 또는 ensemble 이어야 합니다.")
            mode = raw_mode
        ens = import_module("class.knee.scroll_ensemble") if mode == "ensemble" else None
        if ens is not None and not ens.enabled():
            return _err(request_id, 503, "ensemble_disabled", _ENSEMBLE_DISABLED, score_mode=mode)
        uploads = [v for k, v in items if k == "frames"]
        if not all(hasattr(u, "read") for u in uploads):
            return _err(request_id, 400, "frame_not_file", "frames 는 파일이어야 합니다.", score_mode=mode)
        include_overlay = True
        if "include_overlay" in keys:
            raw = form.get("include_overlay")
            if not isinstance(raw, str) or raw.strip().lower() not in ("true", "false"):
                return _err(request_id, 400, "invalid_boolean", "include_overlay 는 true 또는 false 여야 합니다.", score_mode=mode)
            include_overlay = raw.strip().lower() == "true"
        manifest = sa.parse_manifest(form.get("manifest"), len(uploads))
        if ens is not None and (manifest["capture_intent"] != "scroll" or manifest["image_scope"] != "full_slice"):
            return _err(request_id, 422, "unsupported_ensemble_input",
                        "앙상블(B+v4)은 '넘기며 분석' + 박스 범위 '영상 한 장 전체'에서만 씁니다. 이 입력은 '기존 B'로 분석해 주세요.", score_mode=mode)
        blobs, total = [], 0
        for u in uploads:   # 장별 한도도 실제로 읽은 바이트로 다시 확인
            data = await u.read(sa.MAX_FRAME_BYTES + 1)
            total += len(data)
            if len(data) > sa.MAX_FRAME_BYTES or total > sa.MAX_TOTAL_BYTES:
                return _err(request_id, 413, "payload_too_large", "장별 1MiB·전체 16MiB 를 넘었습니다.", score_mode=mode)
            blobs.append(data)
        unique, received, size = sa.decode_frames(blobs, manifest)
        blobs = None
        kind = sa.choose_model(manifest["capture_intent"], len(unique))
        if ens is not None:
            return await _run_ensemble(request_id, started, sa, ens, manifest, unique, received, size, include_overlay)
        if kind == "single_2d":
            model = import_module("class.knee.single_image").acl_single
            if not model.configured():   # 서버에 한 장 모델 폴더가 설정되지 않음 → 볼륨 모델로 대신하지 않고 503
                return _err(request_id, 503, "model_single_not_ready", _SINGLE_NOT_READY, retryable=False, score_mode=mode)
        else:
            model = import_module("class.knee.acl_volume").acl_volume
        if not _BUSY.try_acquire():
            return _err(request_id, 429, "busy", "다른 분석이 진행 중입니다. 잠시 뒤 다시 시도해 주세요.", retryable=True, score_mode=mode)
        errors = import_module("class.knee.errors")
        grays = [g for _s, g in unique]
        used = list(unique)
        scope = manifest["image_scope"]

        def job():
            try:
                if kind == "single_2d":
                    return model.analyze(grays, include_overlay=include_overlay)
                # 넘기며 분석(CAM_LOCALIZATION_STEP2): 점수는 여러 장 모델 그대로(히트맵 계산 없음), 위치는 별도 ACL 위치 모델 — 같은 실행권·같은 시간 제한 안
                res = model.analyze(grays, include_overlay=False)
                res["localization"] = import_module("class.knee.screen_localization").locate_middle(used, scope, include_overlay)
                return res
            finally:
                _BUSY.release()   # 계산이 실제로 끝난 뒤에만(시간 초과로 먼저 응답했어도)

        try:
            fut = asyncio.get_running_loop().run_in_executor(None, job)
        except Exception:
            _BUSY.release()
            raise
        timeout = float(_setting("KNEE_SCREEN_ANALYSIS_TIMEOUT_S", "60"))
        try:
            result = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.TimeoutError:
            fut.add_done_callback(lambda f: f.cancelled() or f.exception())   # 늦게 끝난 계산의 예외를 회수(경고 방지)
            return _err(request_id, 504, "timeout", "분석 시간이 초과되었습니다. 잠시 뒤 다시 시도해 주세요.", retryable=True, score_mode=mode)
        except errors.KneeModelNotReady:
            if kind == "single_2d":
                return _err(request_id, 503, "model_single_not_ready", _SINGLE_NOT_READY, retryable=False, score_mode=mode)
            return _err(request_id, 503, "model_unavailable", "분석 모델을 지금 준비하지 못했습니다. 잠시 뒤 다시 시도해 주세요.", retryable=True, score_mode=mode)
        except errors.KneeInputError:
            return _err(request_id, 422, "invalid_image", "이미지를 분석할 수 없습니다.", score_mode=mode)
        body_out = sa.build_response(request_id=request_id, manifest=manifest, received=received, unique=unique, size=size, model=model,
                                     result=result, latency_ms=int((time.perf_counter() - started) * 1000), device=model.device, kind=kind)
        if kind == "volume":
            body_out.update(score_mode="legacy", runtime_profile_id=None, ensemble=None)   # 추가 필드만(기존 B 응답 의미 그대로)
        unique = result = grays = used = None
        return JSONResponse(content=body_out)
    except Exception as exc:
        sa_err = getattr(sa, "ScreenAnalysisError", None)
        if sa_err is not None and isinstance(exc, sa_err):
            return _err(request_id, exc.status, exc.code, exc.message, exc.retryable, score_mode=mode)
        _log.error("knee screen-analysis failed request_id=%s error_type=%s", request_id, type(exc).__name__)
        return _err(request_id, 500, "internal_error", "서버 처리 중 문제가 생겼습니다.", score_mode=mode)
    finally:
        if form is not None:
            try:
                await form.close()
            except Exception:
                pass


async def _run_ensemble(request_id, started, sa, ens, manifest, unique, received, size, include_overlay):
    """앙상블(B+v4) 넘기며 분석. ① 로컬 묶음 설정·manifest 점검(무효면 503) ② 고유 15장 미만은 모델 없이 not_evaluated
    ③ 그 밖은 기존과 같은 실행권(_BUSY)·시간 제한 안에서 계산."""
    mode = "ensemble"
    try:   # 모델을 건드리기 전에 로컬 묶음 설정·manifest 부터(없거나 무효면 HF 다운로드·로드 없이 503) — ENSEMBLE-LOCAL-001
        ens.check_bundle_config()
    except ens.EnsembleUnavailable as exc:
        _log.warning("knee ensemble unavailable request_id=%s reason=%s", request_id, exc.reason)
        return _err(request_id, 503, "ensemble_unavailable", _ENSEMBLE_UNAVAILABLE, retryable=False, score_mode=mode)
    if len(unique) < ens.MIN_UNIQUE:   # 장을 반복·보간해 채우지 않는다. 모델도 부르지 않는다
        body = sa.build_ensemble_response(request_id=request_id, manifest=manifest, received=received, size=size, include_overlay=include_overlay,
                                          result={"status": "unavailable", "reason": "insufficient_ensemble_frames", "unique_count": len(unique),
                                                  "used_count": 0, "used_sequences": [], "selection_start": None, "n_valid": None,
                                                  "required": ens.required_valid(ens.K), "frames": [], "middle": None, "score": None, "margin": None},
                                          latency_ms=int((time.perf_counter() - started) * 1000))
        return JSONResponse(content=body)
    if not _BUSY.try_acquire():
        return _err(request_id, 429, "busy", "다른 분석이 진행 중입니다. 계산이 끝난 뒤 다시 시도해 주세요(진행 중인 계산은 중간에 멈출 수 없습니다).",
                    retryable=True, score_mode=mode)
    used = list(unique)

    def job():
        try:
            res = ens.analyze(used)
            res["localization"] = sa.ensemble_localization(res, manifest["image_scope"], include_overlay)
            return res
        finally:
            _BUSY.release()   # 계산이 실제로 끝난 뒤에만(시간 초과로 먼저 응답했어도)

    try:
        fut = asyncio.get_running_loop().run_in_executor(None, job)
    except Exception:
        _BUSY.release()
        raise
    timeout = float(_setting("KNEE_SCREEN_ANALYSIS_TIMEOUT_S", "60"))
    try:
        result = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
    except asyncio.TimeoutError:
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())
        return _err(request_id, 504, "timeout", "분석 시간이 초과되었습니다. 계산이 끝날 때까지 다음 분석은 기다려야 합니다.", retryable=True, score_mode=mode)
    except ens.EnsembleUnavailable as exc:
        _log.warning("knee ensemble unavailable request_id=%s reason=%s", request_id, exc.reason)
        return _err(request_id, 503, "ensemble_unavailable", _ENSEMBLE_UNAVAILABLE, retryable=False, score_mode=mode)
    except import_module("class.knee.errors").KneeInputError:
        return _err(request_id, 422, "invalid_image", "이미지를 분석할 수 없습니다.", score_mode=mode)
    body = sa.build_ensemble_response(request_id=request_id, manifest=manifest, received=received, size=size, include_overlay=include_overlay,
                                      result=result, latency_ms=int((time.perf_counter() - started) * 1000))
    result = used = None
    return JSONResponse(content=body)
