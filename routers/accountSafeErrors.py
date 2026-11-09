"""답(복구 질문)이 들어가는 주소 전용 오류 응답·로그 정책 (복구 B안 계획 3-4, Codex 001·RB01).

대상: POST /signup, POST /find-pw/verify, POST /find-pw, GET·PUT /me/recovery-questions.
공용 exception_handlers.py 는 바꾸지 않는다. 이 route 클래스가 처리기 안의 모든 오류를 직접 응답으로 바꿔서 공용 처리기로 넘기지 않는다.

- 422: {"ok": false, "detail": [{"loc": 허용 위치, "code": 고정 코드}]}
  - 위치는 주소별 **정적 허용 목록**만 쓴다. 구조화된 loc 를 조각 단위(정확한 str·int 타입)로 비교하고, 요청 키로 문자열을 만들지 않는다.
  - 목록 밖 위치(알 수 없는 키 이름·범위 밖 번호 포함)는 ["body"] 로 접는다. 사용자가 보낸 키 이름은 내보내지 않는다.
  - 코드는 서버가 정한 오류 종류 → 고정 코드 표로만 정한다. 모르는 종류는 invalid_value.
  - pydantic 의 input·ctx·msg·예외 문구는 응답·로그에 싣지 않는다.
  - 본문 JSON 해석 오류·본문 형식 오류·중첩 모델 오류도 같은 경로(RequestValidationError)로 처리한다.
- 500: 고정 문구. 로그에는 주소 이름(서버 정의)·분류·예외 클래스 이름만 남긴다(예외 문자열·스택·SQL 매개변수 0).
- 모든 상태 Cache-Control: no-store.
- 유지보수: 프로세스 환경 변수 ACCOUNT_RECOVERY_MAINTENANCE=1 이면 **본문 해석·DB 세션·bcrypt 보다 먼저** 고정 503 을 준다(계획 2-3·2-4, D8).
  숨은 우회 키는 없다.
"""
import logging
import os

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, ProgrammingError, SQLAlchemyError

_log = logging.getLogger("account_api")

MAINTENANCE_ENV = "ACCOUNT_RECOVERY_MAINTENANCE"
SERVER_ERROR = "서버 처리 중 문제가 생겼습니다."
MAINTENANCE = "지금은 점검 중이에요. 잠시 후 다시 시도해 주세요."
MAX_DETAILS = 12

_BODY = ("body",)


def _answer_locs(field: str) -> set[tuple]:
    base = ("body", field)
    locs = {base}
    for i in range(3):
        locs.add(base + (i,))
        locs.add(base + (i, "question_id"))
        locs.add(base + (i, "answer"))
    return locs


def _fields(*names: str) -> set[tuple]:
    return {("body", name) for name in names}


SIGNUP_LOCS = frozenset({_BODY} | _fields("user_id", "user_pw", "user_name", "user_birth", "user_un", "user_sp") | _answer_locs("recovery_answers"))
VERIFY_LOCS = frozenset({_BODY} | _fields("user_id", "user_name", "user_birth") | _answer_locs("answers"))
RESET_LOCS = frozenset(VERIFY_LOCS | _fields("user_pw", "user_pw_confirm"))
PUT_LOCS = frozenset({_BODY} | _fields("current_password") | _answer_locs("answers"))
BODYLESS_LOCS = frozenset({_BODY})

# (메서드, 주소) → (로그용 고정 이름, 허용 위치). 목록에 없는 주소는 ["body"] 만 허용한다(안전 쪽 기본값).
ROUTE_POLICY: dict[tuple[str, str], tuple[str, frozenset]] = {
    ("POST", "/signup"): ("signup", SIGNUP_LOCS),
    ("POST", "/find-pw/verify"): ("find_pw_verify", VERIFY_LOCS),
    ("POST", "/find-pw"): ("find_pw_reset", RESET_LOCS),
    ("PUT", "/me/recovery-questions"): ("recovery_questions_put", PUT_LOCS),
    ("GET", "/me/recovery-questions"): ("recovery_questions_get", BODYLESS_LOCS),
}

# 서버가 정한 오류 종류 → 고정 코드. 여기 없는 종류는 invalid_value.
_CODE_BY_TYPE = {
    "missing": "missing",
    "extra_forbidden": "not_allowed_field",
    "json_invalid": "invalid_body",
    "json_type": "invalid_body",
    "model_attributes_type": "invalid_type",
    "model_type": "invalid_type",
    "dict_type": "invalid_type",
    "list_type": "invalid_type",
    "string_type": "invalid_type",
    "answer_type": "invalid_type",
    "password_type": "invalid_type",
    "answer_empty": "empty",
    "signup_empty": "empty",
    "string_too_short": "too_short",
    "password_too_short": "too_short",
    "signup_too_short": "too_short",
    "string_too_long": "too_long",
    "answer_too_long": "too_long",
    "password_too_long": "too_long",
    "signup_too_long": "too_long",
    "answer_control": "invalid_value",
    "literal_error": "invalid_value",
    "string_pattern_mismatch": "invalid_value",
    "value_error": "invalid_value",
    "birth_invalid": "invalid_value",
    "birth_future": "future_date",
    "birth_under_age": "under_age",
    "answers_duplicate": "answers_duplicate",
}
_ANSWER_CONTAINERS = {("body", "answers"), ("body", "recovery_answers")}
ALL_CODES = frozenset(set(_CODE_BY_TYPE.values()) | {"invalid_value", "answers_count"})


def maintenance_on() -> bool:
    return os.environ.get(MAINTENANCE_ENV, "").strip() == "1"


def safe_loc(loc: object, allowed: frozenset) -> tuple:
    """구조화된 loc 를 조각 단위로 검사한다. 각 조각이 정확히 str 또는 int(bool 제외)이고 전체가 허용 목록에 있을 때만 그대로 쓴다."""
    if not isinstance(loc, (tuple, list)) or not loc:
        return _BODY
    pieces = []
    for piece in loc:
        if type(piece) is str or type(piece) is int:
            pieces.append(piece)
        else:
            return _BODY
    candidate = tuple(pieces)
    # 허용 목록 원소와 조각 타입까지 같은지 본다(1 == True 같은 느슨한 비교를 피한다).
    for allowed_loc in allowed:
        if len(allowed_loc) == len(candidate) and all(type(a) is type(b) and a == b for a, b in zip(allowed_loc, candidate)):
            return allowed_loc
    return _BODY


def safe_code(error_type: object, loc: tuple) -> str:
    if type(error_type) is not str:
        return "invalid_value"
    if loc in _ANSWER_CONTAINERS and error_type in ("too_short", "too_long"):
        return "answers_count"
    if error_type == "extra_forbidden":
        return "not_allowed_field"
    return _CODE_BY_TYPE.get(error_type, "invalid_value")


def safe_details(exc: RequestValidationError, allowed: frozenset) -> list[dict]:
    out: list[dict] = []
    seen: set = set()
    try:
        errors = exc.errors()
    except Exception:
        errors = []
    for error in errors if isinstance(errors, (list, tuple)) else []:
        if not isinstance(error, dict):
            continue
        loc = safe_loc(error.get("loc"), allowed)
        code = safe_code(error.get("type"), loc)
        key = (loc, code)
        if key in seen:
            continue
        seen.add(key)
        out.append({"loc": list(loc), "code": code})
        if len(out) >= MAX_DETAILS:
            break
    return out or [{"loc": list(_BODY), "code": "invalid_value"}]


def _category(exc: Exception) -> str:
    if isinstance(exc, IntegrityError):
        return "db_integrity"
    if isinstance(exc, ProgrammingError):
        return "db_programming"
    if isinstance(exc, OperationalError):
        return "db_operational"
    if isinstance(exc, (DBAPIError, SQLAlchemyError)):
        return "db_other"
    return "unexpected"


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


class AnswerSafeRoute(APIRoute):
    """답이 들어가는 주소의 route 클래스. 유지보수 차단 → 처리기(본문 해석·의존성 포함) → 안전 오류 변환 순서다."""

    def get_route_handler(self):
        handler = super().get_route_handler()
        methods = sorted(self.methods or [])
        policy = ROUTE_POLICY.get((methods[0], self.path)) if len(methods) == 1 else None
        name, allowed = policy if policy else ("answer_route", BODYLESS_LOCS)

        async def safe_handler(request: Request):
            # 유지보수 검사는 본문을 읽기 전, DB 세션 의존성·bcrypt 보다 먼저 한다.
            if maintenance_on():
                return _no_store(JSONResponse(status_code=503, content={"ok": False, "code": "maintenance", "detail": MAINTENANCE}))
            try:
                response = await handler(request)
            except RequestValidationError as exc:
                details = safe_details(exc, allowed)
                _log.info("%s rejected status=422 count=%d", name, len(details))
                response = JSONResponse(status_code=422, content={"ok": False, "detail": details})
            except HTTPException as exc:
                response = JSONResponse(status_code=exc.status_code, content={"ok": False, "detail": exc.detail})
            except Exception as exc:
                _log.error("%s failed status=500 category=%s error_type=%s", name, _category(exc), type(exc).__name__)
                response = JSONResponse(status_code=500, content={"ok": False, "detail": SERVER_ERROR})
            return _no_store(response)

        return safe_handler


# 계획 문서의 이름(가입 주소용). 같은 정책을 쓴다.
SignupSafeRoute = AnswerSafeRoute
