"""마이페이지용 본인 조회(GET /me) 응답 형식. 허용 필드만 명시한다.

비밀번호·해시·생년월일·관리자 등급·영상 주소·병명·기준 박스·소견 JSON 은 어떤 필드에도 두지 않는다(extra="forbid").
"""
from pydantic import BaseModel, ConfigDict


class MeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idx: int
    user_id: str
    user_name: str | None = None
    user_un: str | None = None   # 학교
    user_sp: str | None = None   # 전공


class RecentSubmission(BaseModel):
    """최근 저장된 문제 제출 한 건. 이미지·병명·기준 박스·소견은 담지 않는다."""
    model_config = ConfigDict(extra="forbid")

    review_idx: int
    study_idx: int
    part_label: str | None = None    # 문제가 삭제·누락되면 None (현재 열람 불가)
    modal_label: str | None = None
    study_available: bool
    solution: str | None = None      # C / H / W, 그 밖은 None
    solution_label: str              # 정답 / 부분정답 / 오답 / 결과 없음
    created_at: str | None = None    # DB 에 기록된 저장 시각(시간대를 덧붙이지 않는다)


class RecentLearning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int
    items: list[RecentSubmission]


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    profile: MeProfile
    recent_learning: RecentLearning
