"""마이페이지 '내 정보 수정·비밀번호 변경' 요청/응답 형식. 모든 모델 extra="forbid"(알 수 없는 필드는 422).

요청에는 회원 번호·아이디·생년월일·권한·승인 표시·탈퇴 표시 같은 필드가 없다 — 회원은 서버 세션의 learner 로만 정한다.
비밀번호(현재/새)는 앞뒤 공백을 지우지 않고 UTF-8 72바이트 이내만 검사한다(NFC 변환 없음). 응답에는 비밀번호·해시·생년월일을 담지 않는다.
설계: designs/ACCOUNT-FIND-MYPAGE-20261105/FOLLOWUP2_DESIGN.md 4절.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from schemas.accountProfileSchemas import MeProfile

# 제어문자(줄바꿈·탭 포함)와 유니코드 줄/문단 구분자는 이름·학교·전공에 넣을 수 없다.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def _text(value: str, label: str, max_len: int) -> str:
    if _CONTROL.search(value):   # 앞뒤에 붙은 줄바꿈·탭도 strip 으로 지우지 않고 거절한다(붙여넣기 실수를 조용히 고치지 않음)
        raise ValueError(f"{label}에 사용할 수 없는 문자가 있어요.")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label}을(를) 입력해 주세요.")
    if len(cleaned) > max_len:
        raise ValueError(f"{label}은(는) {max_len}자 이내로 입력해 주세요.")
    if _CONTROL.search(cleaned):
        raise ValueError(f"{label}에 사용할 수 없는 문자가 있어요.")
    return cleaned


def _password(value: str, label: str, min_len: int = 1) -> str:
    if len(value) < min_len:
        raise ValueError(f"{label}은(는) {min_len}글자 이상이어야 해요.")
    if len(value) > 72 or len(value.encode("utf-8")) > 72:
        raise ValueError(f"{label}은(는) UTF-8 기준 72바이트 이내로 입력해 주세요.")
    return value


class ProfilePatch(BaseModel):
    """이름·학교·전공 전체 + 현재 비밀번호 + 불러왔을 때의 버전. 일부만 보내는 요청은 허용하지 않는다."""
    model_config = ConfigDict(extra="forbid")

    user_name: StrictStr
    user_un: StrictStr
    user_sp: StrictStr
    current_password: StrictStr
    expected_version: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("user_name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _text(value, "이름", 10)

    @field_validator("user_un")
    @classmethod
    def _school(cls, value: str) -> str:
        return _text(value, "학교", 20)

    @field_validator("user_sp")
    @classmethod
    def _major(cls, value: str) -> str:
        return _text(value, "전공", 20)

    @field_validator("current_password")
    @classmethod
    def _current(cls, value: str) -> str:
        return _password(value, "현재 비밀번호")


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: StrictStr
    new_password: StrictStr
    new_password_confirm: StrictStr

    @field_validator("current_password")
    @classmethod
    def _current(cls, value: str) -> str:
        return _password(value, "현재 비밀번호")

    @field_validator("new_password", "new_password_confirm")
    @classmethod
    def _new(cls, value: str) -> str:
        return _password(value, "새 비밀번호", min_len=4)


class SettingsResponse(BaseModel):
    """GET /me/settings, PATCH /me/profile 의 응답. profile 은 기존 /me 의 5필드 그대로, profile_version 은 불투명한 충돌 확인용 문자열."""
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    profile: MeProfile
    profile_version: str = Field(pattern=r"^[0-9a-f]{64}$")


class PasswordChangedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True
    password_changed: Literal[True] = True
    session_ended: Literal[True] = True
