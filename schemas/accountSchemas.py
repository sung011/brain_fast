"""학습자 계정찾기 입력값. 기존 회원 스키마와 분리한다.

비밀번호 찾기(verify·reset)는 복구 B안: 신원(아이디·이름·생년월일) + 질문 답 3개(answers)가 모두 필요하다.
답 없이 신원 3항목만 보내면 422 다(약한 fallback 없음). 아이디 찾기(FindId)는 그대로다.
"""
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.accountRecoverySchemas import RecoveryAnswers
from schemas.signupEmailSchemas import EMAIL_MAX_LENGTH


class FindId(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_name: str = Field(min_length=1, max_length=10)
    user_birth: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("user_name", "user_birth", mode="before")
    @classmethod
    def strip_identity(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("user_birth")
    @classmethod
    def validate_birth(cls, value: str) -> str:
        try:
            birth = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("올바른 생년월일을 입력해 주세요.") from exc
        if birth > date.today():
            raise ValueError("생년월일은 오늘 이전 날짜로 입력해 주세요.")
        return value


class VerifyAccount(FindId):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    user_id: str = Field(min_length=1, max_length=EMAIL_MAX_LENGTH)
    answers: RecoveryAnswers

    @field_validator("user_id", mode="before")
    @classmethod
    def strip_id(cls, value):
        return value.strip() if isinstance(value, str) else value


class FindPw(VerifyAccount):
    # 기존 회원 규칙(4자 이상). 비밀번호의 앞뒤 공백은 제거하지 않는다.
    user_pw: str = Field(min_length=4, max_length=72)
    user_pw_confirm: str = Field(min_length=4, max_length=72)

    @field_validator("user_pw", "user_pw_confirm")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("비밀번호는 UTF-8 기준 72바이트 이내로 입력해 주세요.")
        return value

