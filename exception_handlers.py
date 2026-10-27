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
import re
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


# ── 로그에 비밀번호가 남지 않게 가린다(LOGIN-019-01, 사용자 승인 2026-10-13: A) ───────────────────────────────────
# 응답(사용자에게 돌려주는 JSON)은 그대로 두고, **로그에 적기 전에** 비밀번호·토큰 같은 값만 "[REDACTED]" 로 바꾼다.
# 가림 처리가 실패하면 원문을 쓰지 않고 안전한 고정 문구로 닫는다.
REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "plainpw", "hashedpw", "dbuserpw", "pwmatch", "userpw", "oldpw", "newpw", "pw", "password", "passwd", "secret",
    "token", "accesstoken", "refreshtoken", "problemtoken", "authorization", "apikey",
}
_KEY_ALTERNATIVES = (
    r"plain[_-]?pw|hashed[_-]?pw|db[_-]?user[_-]?pw|pw[_-]?match|user[_-]?pw|old[_-]?pw|new[_-]?pw|pw|password|passwd|secret|"
    r"(?:access|refresh|problem)[_-]?token|token|authorization|api[_-]?key"
)
# 글자 모양 `user_pw='값'`, `"plain_pw": "값"`, `password=값` 처럼 키 뒤에 값이 오는 부분을 찾는다. 이미 가려진 값은 건너뛴다(여러 번 해도 같음).
# 값이 `Bearer 토큰`·`Basic 값` 처럼 공백이 있는 형태(Authorization 헤더)여도 종류 이름 뒤의 토큰까지 함께 가린다(FINDING-LOG-021-02).
# 쿼리 문자열(`token=값&page=2`)에서 `&` 뒤의 다른 값까지 가리지 않도록 `&` 에서 멈춘다.
_TEXT_SECRET = re.compile(
    r"""(["']?\b(?:%s)\b["']?\s*[:=]\s*)(?!\[REDACTED\])(?:'(?:[^'\x5c]|\x5c.)*'|"(?:[^"\x5c]|\x5c.)*"|(?:Bearer|Basic|Digest)[ \t]+[^\s,;}\])&'"]+|[^\s,;}\])&]+)""" % _KEY_ALTERNATIVES,
    re.IGNORECASE,
)
# 키 이름 없이 글 속에 그대로 나온 `Bearer <토큰>`(예외 메시지 등). 8자 이상의 토큰 모양만 가려서 일반 문장("the bearer of news")은 그대로 둔다.
_BEARER_TOKEN = re.compile(r"\b(bearer)([ \t]+)(?!\[REDACTED\])[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)


def _key_is_sensitive(key: Any) -> bool:
    return re.sub(r"[^a-z0-9]", "", str(key).lower()) in _SENSITIVE_KEYS


def scrub_text(text: str) -> str:
    """글자 안의 `키=값` 형태 비밀 값을 가린다. 실패하면 원문 대신 고정 문구를 돌려준다."""
    try:
        text = _TEXT_SECRET.sub(lambda m: m.group(1) + REDACTED, text)
        return _BEARER_TOKEN.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)
    except Exception:
        return "[REDACTED: 가림 처리 실패]"


def redact_sensitive(value: Any) -> Any:
    """로그에 적을 값에서 비밀번호·토큰 같은 값을 가린다(dict/list 는 재귀, 글자는 `키=값` 형태, 그 밖은 그대로).

    비밀 키(plain_pw, hashed_pw, db_user_pw, pw_match, user_pw, password, token, authorization 등, 대소문자·`_`·`-` 무시)의 값은 통째로 가린다.
    비밀번호와 무관한 값은 그대로 둔다. 어떤 경우에도 예외를 내지 않으며, 처리하지 못하면 원문 대신 고정 문구를 돌려준다.
    """
    try:
        if isinstance(value, dict):
            return {k: (REDACTED if _key_is_sensitive(k) else redact_sensitive(v)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [redact_sensitive(item) for item in value]
        if isinstance(value, str):
            return scrub_text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return scrub_text(str(value))   # 예외 객체 등 그 밖의 값은 글자로 바꿔 가린다
    except Exception:
        return "[REDACTED: 가림 처리 실패]"


def _request_info(request: Request) -> str:
    """로그에 넣을 요청 요약(메서드, 경로, 쿼리, 클라이언트 IP)을 만든다."""
    client = request.client.host if request.client else "-"
    query = scrub_text(request.url.query) if request.url.query else "(없음)"   # `?token=…` 같은 쿼리 값도 가린다
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
    """JSON 직렬화 불가 값(bytes, ValueError 등)을 문자열로 바꾼다."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _validation_lines(errors: list[dict[str, Any]]) -> str:
    """Pydantic/FastAPI 검증 오류 목록을 사람이 읽기 쉬운 여러 줄로 바꾼다."""
    if not errors:
        return "  (상세 항목 없음)"
    lines: list[str] = []
    for i, err in enumerate(errors, start=1):
        loc = err.get("loc") or ()
        where = " → ".join(str(part) for part in loc) or "(알 수 없음)"
        # 비밀번호 같은 필드의 입력값, 그리고 입력 전체(dict)에 들어 있는 비밀 값은 로그에 찍지 않는다.
        shown = REDACTED if any(_key_is_sensitive(part) for part in loc) else redact_sensitive(err.get("input"))
        lines.append(
            f"  {i}) 위치={where} | 입력값={shown!r} | 원인={scrub_text(str(err.get('msg')))}"
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
        f"오류 내용 : {redact_sensitive(exc.detail)}\n"
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
    error_logger.error(scrub_text(report))   # 예외 메시지·스택 글자에 `user_pw=값` 같은 비밀이 섞여 있어도 가린다
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error_type": "server_error",
            "message": "서버 오류가 발생했습니다. logs/날짜폴더/error.log 를 확인하세요.",
        },
    )
