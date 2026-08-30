"""
FastAPI 예외 처리 모듈.

역할:
- 요청 처리 중 오류가 나면 사용자에게 JSON으로 알려 주고
- 같은 오류 내용을 로그 파일에도 자세히 남긴다.

다루는 오류 종류:
1) 입력값 검증 실패 (422)  → validation_exception_handler
2) HTTPException (4xx/5xx) → http_exception_handler
3) 예상 못 한 서버 예외    → unhandled_exception_handler
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from logging_setup import ERROR_LOGGER

# logging_setup.py 에서 만들어 둔 오류 로거. 여기로 쓰면 날짜 폴더 파일에 저장된다.
error_logger = logging.getLogger(ERROR_LOGGER)

# 우리 프로젝트 루트. 스택에서 "우리 코드가 있는 줄"을 찾을 때 사용한다.
APP_ROOT = Path(__file__).resolve().parent


def _request_info(request: Request) -> str:
    """로그에 넣을 요청 요약(메서드, 경로, 쿼리, 클라이언트 IP)을 만든다."""
    client = request.client.host if request.client else "-"
    query = request.url.query or "(없음)"
    return (
        f"요청      : {request.method} {request.url.path}\n"
        f"쿼리      : {query}\n"
        f"클라이언트: {client}"
    )


def _app_location(exc: BaseException) -> str:
    """
    예외가 난 우리 코드 위치를 찾아 문자열로 만든다.
    라이브러리(site-packages) 줄은 건너뛰고, 프로젝트 파일/함수/줄/코드를 남긴다.
    """
    frames = traceback.extract_tb(exc.__traceback__)
    app_frame = None
    for frame in frames:
        if str(APP_ROOT) in frame.filename and "site-packages" not in frame.filename:
            app_frame = frame
    if app_frame is None and frames:
        app_frame = frames[-1]
    if app_frame is None:
        return "위치      : (애플리케이션 코드를 찾지 못함)"
    return (
        f"파일      : {app_frame.filename}\n"
        f"함수      : {app_frame.name}\n"
        f"줄        : {app_frame.lineno}\n"
        f"코드      : {app_frame.line or '(소스 없음)'}"
    )


def _json_safe(value: Any) -> Any:
    """JSON 직렬화 불가 값(bytes 등)을 문자열로 바꾼다."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _validation_lines(errors: list[dict[str, Any]]) -> str:
    """Pydantic/FastAPI 검증 오류 목록을 사람이 읽기 쉬운 여러 줄로 바꾼다."""
    if not errors:
        return "  (상세 항목 없음)"
    lines: list[str] = []
    for i, err in enumerate(errors, start=1):
        loc = err.get("loc") or ()
        where = " → ".join(str(part) for part in loc) or "(알 수 없음)"
        lines.append(
            f"  {i}) 위치={where} | 입력값={err.get('input')!r} | 원인={err.get('msg')}"
        )
    return "\n".join(lines)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    입력값이 스키마와 다를 때 (예: /items/abc 처럼 숫자가 와야 하는데 문자).
    - 로그: 어느 필드가 왜 잘못됐는지 기록
    - 응답: HTTP 422 JSON
    """
    errors = _json_safe(exc.errors())
    report = (
        "\n"
        "========== 입력값 오류 ==========\n"
        f"{_request_info(request)}\n"
        "상태코드  : 422\n"
        "오류 내용 :\n"
        f"{_validation_lines(errors)}\n"
        "================================"
    )
    error_logger.error(report)
    return JSONResponse(
        status_code=422,
        content={"ok": False, "error_type": "validation_error", "detail": errors},
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """
    raise HTTPException(...) 로 일부러 올린 HTTP 오류를 처리한다.
    - 3xx + Location: 리다이렉트
    - 404(없는 주소)는 흔해서 로그에 남기지 않는다.
    - 그 외(400, 401, 500 등)는 오류 내용을 로그에 남긴다.
    """
    location = (exc.headers or {}).get("Location")
    if 300 <= exc.status_code < 400 and location:
        return RedirectResponse(url=location, status_code=exc.status_code)

    if exc.status_code == 404:
        return JSONResponse(status_code=404, content={"ok": False, "detail": exc.detail})

    report = (
        "\n"
        "========== HTTP 오류 ==========\n"
        f"{_request_info(request)}\n"
        f"상태코드  : {exc.status_code}\n"
        f"오류 내용 : {exc.detail}\n"
        f"{_app_location(exc)}\n"
        "=============================="
    )
    error_logger.error(report)
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error_type": "http_error", "detail": exc.detail},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    코드에서 잡지 못한 예외(버그, RuntimeError 등)를 처리한다.
    - 로그: 종류, 메시지, 파일/줄, 전체 스택을 남긴다.
    - 응답: 500. 내부 스택은 클라이언트에 노출하지 않는다.
    """
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    report = (
        "\n"
        "========== 서버 오류 ==========\n"
        f"{_request_info(request)}\n"
        "상태코드  : 500\n"
        f"오류 종류 : {type(exc).__name__}\n"
        f"오류 내용 : {exc}\n"
        f"{_app_location(exc)}\n"
        "스택:\n"
        f"{tb.rstrip()}\n"
        "=============================="
    )
    error_logger.error(report)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error_type": "server_error",
            "message": "서버 오류가 발생했습니다. logs/날짜폴더/error.log 를 확인하세요.",
        },
    )
