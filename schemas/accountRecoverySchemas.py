"""비밀번호 찾기 질문(복구 B안) 입력·응답 형식. 설계: FOLLOWUP2_DESIGN.md 3-B·3-C, 계획 FOLLOWUP2_RECOVERY_B_PLAN.md r2.

- 질문은 고정 3개(security_questions_v1: Q01·Q02·Q03). 답은 3개 모두 필요하고 ID 중복·누락은 422.
- 답 정규화 answer_norm_v1: 문자열만, 원문 1~256자, 줄바꿈·제어문자 거절 → NFC → 앞뒤 공백 제거 → 유니코드 공백 연속을 공백 1개로 → ASCII A–Z 만 소문자.
  정규화 뒤 1~50자이고 UTF-8 72바이트 이내. 비밀번호에는 적용하지 않는다.
- 정규화한 답은 SecretStr 로 감싸 repr·로그에 값이 나오지 않게 한다. 해시·DB 는 이 검사를 통과한 뒤에만 한다.
- 오류 종류(type)는 서버가 정한 이름만 쓴다(routers/accountSafeErrors.py 가 고정 코드로 바꾼다).
"""
import re
import unicodedata
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, StrictStr, field_validator
from pydantic_core import PydanticCustomError

QUESTION_SET = "security_questions_v1"
NORM_VERSION = "answer_norm_v1"
QUESTION_IDS = ("Q01", "Q02", "Q03")
QUESTION_LABELS = {
    "Q01": "처음 다닌 초등학교 이름",
    "Q02": "어릴 때 사용하던 별명",
    "Q03": "어릴 때 살았던 동네 이름",
}

RAW_MAX_CHARS = 256
NORM_MAX_CHARS = 50
NORM_MAX_BYTES = 72

# 제어문자(C0·DEL·C1, 줄바꿈·탭 포함)와 유니코드 줄/문단 구분자.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_SPACES = re.compile(r"\s+")
_ASCII_LOWER = {code: code + 32 for code in range(ord("A"), ord("Z") + 1)}


def normalize_answer(value: object) -> str:
    """answer_norm_v1. 실패하면 PydanticCustomError(서버 정의 type)를 낸다. 메시지에 입력값을 넣지 않는다."""
    if not isinstance(value, str):
        raise PydanticCustomError("answer_type", "답은 글자로 입력해 주세요.")
    if len(value) == 0:
        raise PydanticCustomError("answer_empty", "답을 입력해 주세요.")
    if len(value) > RAW_MAX_CHARS:
        raise PydanticCustomError("answer_too_long", "답이 너무 길어요.")
    if _CONTROL.search(value):
        raise PydanticCustomError("answer_control", "답에 사용할 수 없는 문자가 있어요.")
    text = unicodedata.normalize("NFC", value).strip()
    text = _SPACES.sub(" ", text)
    text = text.translate(_ASCII_LOWER)
    if not text:
        raise PydanticCustomError("answer_empty", "답을 입력해 주세요.")
    if len(text) > NORM_MAX_CHARS or len(text.encode("utf-8")) > NORM_MAX_BYTES:
        raise PydanticCustomError("answer_too_long", "답이 너무 길어요.")
    return text


class RecoveryAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    question_id: Literal["Q01", "Q02", "Q03"]
    answer: SecretStr

    @field_validator("answer", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> str:
        return normalize_answer(value)


def _exact_question_set(answers: list[RecoveryAnswer]) -> list[RecoveryAnswer]:
    ids = [item.question_id for item in answers]
    if sorted(ids) != list(QUESTION_IDS):
        raise PydanticCustomError("answers_duplicate", "질문마다 답을 하나씩 입력해 주세요.")
    return sorted(answers, key=lambda item: item.question_id)


# 정확히 3개(Q01·Q02·Q03 각 1개). 개수가 다르면 too_short/too_long, ID 중복이면 answers_duplicate.
RecoveryAnswers = Annotated[list[RecoveryAnswer], Field(min_length=3, max_length=3), AfterValidator(_exact_question_set)]


def answer_map(answers: list[RecoveryAnswer]) -> dict[str, str]:
    """질문 ID → 정규화한 답. 서비스 안에서만 쓰고 응답·로그에 내보내지 않는다."""
    return {item.question_id: item.answer.get_secret_value() for item in answers}


def _password(value: object, label: str, min_len: int = 1) -> str:
    if not isinstance(value, str):
        raise PydanticCustomError("password_type", f"{label}을(를) 글자로 입력해 주세요.")
    if len(value) < min_len:
        raise PydanticCustomError("password_too_short", f"{label}이(가) 너무 짧아요.")
    if len(value) > 72 or len(value.encode("utf-8")) > 72:
        raise PydanticCustomError("password_too_long", f"{label}은(는) UTF-8 기준 72바이트 이내로 입력해 주세요.")
    return value


class RecoveryQuestionsPut(BaseModel):
    """PUT /me/recovery-questions. 현재 비밀번호 + 답 3개. 회원은 서버 세션으로만 정한다."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    current_password: StrictStr
    answers: RecoveryAnswers

    @field_validator("current_password")
    @classmethod
    def _current(cls, value: str) -> str:
        return _password(value, "현재 비밀번호")


class RecoveryStatus(BaseModel):
    """GET·PUT /me/recovery-questions 응답. 등록 여부만 알려 주고 답·해시는 담지 않는다."""
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    registered: bool
    question_set: Literal["security_questions_v1"] = QUESTION_SET
    question_ids: list[str] = Field(default_factory=lambda: list(QUESTION_IDS))
