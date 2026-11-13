"""가입 인증 메일 주소와 인증번호 형식."""
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

EMAIL_MAX_LENGTH = 254
_LOCAL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}", re.ASCII)
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", re.ASCII)


def normalize_email(value: str) -> str:
    """일반 ASCII 메일 주소를 가입 아이디 형식으로 정규화한다."""
    email = value.strip()
    local, _, domain = email.rpartition("@")
    labels = domain.split(".")
    if (
        len(email) > EMAIL_MAX_LENGTH
        or not _LOCAL.fullmatch(local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or len(labels) < 2
        or not all(_LABEL.fullmatch(label) for label in labels)
    ):
        raise ValueError("올바른 이메일 주소를 입력해 주세요.")
    return email.lower()


class SignupEmailCode(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    email: str = Field(max_length=EMAIL_MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        try:
            return normalize_email(value)
        except ValueError:
            raise PydanticCustomError("email_invalid", "올바른 이메일 주소를 입력해 주세요.") from None


class SignupEmailVerify(SignupEmailCode):
    email_code: str = Field(pattern=r"^[0-9]{8}$")
