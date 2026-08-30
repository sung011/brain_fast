from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class UserLogin(BaseModel):
    """로그인 요청. user_id/user_pw 와 userId/userPw 모두 허용."""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(alias="userId")
    user_pw: str = Field(alias="userPw")


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
