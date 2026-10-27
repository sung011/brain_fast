from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class UserLogin(BaseModel):
    """로그인 요청. user_id/user_pw 와 userId/userPw 모두 허용."""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId")
    user_pw: str = Field(alias="userPw")


def _yy_to_year(yy: int) -> int:
    """두 자리 해는 올해보다 크면 1900년대, 아니면 2000년대로 본다. 880324 → 1988."""
    return 1900 + yy if yy > date.today().year % 100 else 2000 + yy


class LearnerSignup(BaseModel):
    """
    학습자 회원가입 POST /signup 요청.
    관리자 세션이 필요 없다. 학교·전공·생년월일을 함께 받는다.
    """

    user_id: str
    user_pw: str
    user_name: str
    user_birth: str
    user_un: str
    user_sp: str

    @field_validator("user_id", "user_name", "user_un", "user_sp", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("user_id")
    @classmethod
    def check_id(cls, value: str) -> str:
        if not value:
            raise ValueError("아이디를 입력해 주세요.")
        if len(value) > 30:
            raise ValueError("아이디는 30자 이내로 입력해 주세요.")
        return value

    @field_validator("user_name")
    @classmethod
    def check_name(cls, value: str) -> str:
        if not value or len(value) > 10:
            raise ValueError("이름은 1~10자로 입력해 주세요.")
        return value

    @field_validator("user_un", "user_sp")
    @classmethod
    def check_school_major(cls, value: str) -> str:
        if not value or len(value) > 20:
            raise ValueError("학교와 전공은 1~20자로 입력해 주세요.")
        return value

    @field_validator("user_pw")
    @classmethod
    def password_bytes(cls, value: str) -> str:
        if len(value) < 4:
            raise ValueError("비밀번호는 4자 이상이어야 합니다.")
        if len(value.encode("utf-8")) > 72:
            raise ValueError("비밀번호는 UTF-8 기준 72바이트 이내로 입력해 주세요.")
        return value

    @field_validator("user_birth", mode="before")
    @classmethod
    def normalize_birth(cls, value):
        if not isinstance(value, str):
            return value
        text = value.strip().replace(".", "-").replace("/", "-").replace(" ", "")
        if len(text) == 8 and text.isdigit():
            text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
        elif len(text) == 6 and text.isdigit():
            text = f"{_yy_to_year(int(text[:2])):04d}-{text[2:4]}-{text[4:]}"
        elif len(text) == 8 and text[2] == "-" and text[5] == "-" and text[:2].isdigit():
            text = f"{_yy_to_year(int(text[:2])):04d}-{text[3:5]}-{text[6:]}"
        return text

    @field_validator("user_birth")
    @classmethod
    def validate_birth(cls, value: str) -> str:
        try:
            birth = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("올바른 생년월일을 입력해 주세요. 예: 2000-01-01") from exc
        today = date.today()
        if birth > today:
            raise ValueError("생년월일은 오늘 이전 날짜로 입력해 주세요.")
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        if age < 14:
            raise ValueError("만 14세 이상 가입할 수 있습니다.")
        return value


class UserCreate(BaseModel):
    """
    관리자 회원가입 POST /admin/user/signup 요청 본문.
    JS(user-signup.js)가 같은 키로 JSON을 보낸다.
    """

    user_id: str = Field(min_length=1, max_length=30)  # 이메일(로그인 아이디)
    user_pw: str = Field(min_length=4, max_length=100)  # 평문. 저장 전 해시
    user_name: str = Field(min_length=1, max_length=10)  # 이름(닉네임)
    user_birth: str = Field(min_length=1, max_length=10)  # YYYY-MM-DD
    user_un: str = Field(min_length=1, max_length=20)  # 학교
    user_sp: str = Field(min_length=1, max_length=20)  # 전공


class UserUpdate(BaseModel):
    """회원 수정 요청. 비밀번호는 입력한 경우에만 변경한다."""

    user_name: str = Field(min_length=1, max_length=10)
    user_birth: str = Field(min_length=1, max_length=10)
    user_un: str = Field(min_length=1, max_length=20)
    user_sp: str = Field(min_length=1, max_length=20)
    mb_level: int = Field(ge=1, le=10)
    user_pw: str | None = Field(default=None, max_length=100)


class UserOut(BaseModel):
    """회원 목록/조회 응답. 비밀번호는 포함하지 않는다."""

    model_config = ConfigDict(from_attributes=True)

    idx: int
    user_id: str
    user_name: str | None
    user_birth: str
    user_un: str
    user_sp: str
    created_at: datetime
    mb_level: int
    state: str

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.strftime("%y년%m월%d일 %H시 %M분")
