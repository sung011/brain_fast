"""비밀번호 찾기 질문(복구 B안) 서비스: 답 해시·검증, 가입과 답 동시 등록, 로그인 후 등록·변경, 등록 상태.

- 해시: 정규화한 답을 utils.password.hash_password(bcrypt cost 12, 답마다 gensalt)로 저장한다. 평문 0.
- 검증: 새 선택형은 등록한 질문 하나의 답을 검증하고, 기존 등록형은 세 답을 모두 검증한다. 각 요청은 더미를 포함해 bcrypt 세 번을 수행한다.
  회원 없음·미등록·행 0~2개·버전 다름·해시 손상 슬롯은 시작 때 1회 만든 유효 더미 해시(cost 12)로 검증해 3회를 맞춘다. 결과는 실패.
  시간 완전 동일은 보장하지 않는다. 시도 제한은 호출자가 bcrypt 보다 먼저 한다.
- 가입: 이메일 확인이 켜져 있으면 일회용 인증번호를 먼저 잠그고 확인한다. 인증번호 소비와 회원·답 저장을 함께 commit 한다. 예외는 전체 rollback.
  user_member 의 user_id unique 위반만 DuplicateUserId(409). 그 밖의 무결성·인프라 오류는 그대로 올려 고정 500 으로 닫는다.
  관리자 userServices.create_user 는 바꾸지 않는다.
- 등록·변경: 승인 learner → (라우터의 엄격 Origin) → learner 5 / IP 30(정보 수정과 공유) → 답 해시 → 회원 잠금 → 현재 비밀번호 → 답 행 교체 → commit.
"""
import re

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import settings

from repositories import accountRecoveryRepositories as repositories
from repositories import accountSettingsRepositories as settings_repositories
from repositories import userRepositories as user_repositories
from schemas.accountRecoverySchemas import CHOICE_QUESTION_SET, NORM_VERSION, QUESTION_IDS, QUESTION_SET, RecoveryQuestionsPut, RecoveryStatus, answer_map
from schemas.userSchemas import LearnerSignup
from services import accountSettingsServices as settings_service
from services import signupEmailServices as signup_email
from services.userServices import format_updated_at
from utils.password import hash_password, verify_password

# bcrypt 해시 모양: $2a$/$2b$/$2y$ + 두 자리 비용 + 53자. 이 모양이 아니면 손상된 슬롯으로 보고 더미로 검증한다.
_BCRYPT = re.compile(r"^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")

# user_member.user_id 의 unique 제약/인덱스 이름. 공유 DB 의 실제 이름은 사전 스키마 대조 도구가 확인한다(다르면 MISMATCH).
USER_ID_UNIQUE_NAMES = frozenset({"user_member_user_id_key"})


class DuplicateUserId(Exception):
    """이미 있는 아이디(삭제 회원 포함) → 409."""


def _make_dummy_hash() -> str:
    # 서비스 시작 때 1회만 만든다(요청마다 gensalt 0). 어떤 답과도 맞지 않도록 정규화 규칙상 거절되는 줄 구분자(U+2028)를 넣는다.
    return hash_password("dummy recovery answer")


DUMMY_HASH = _make_dummy_hash()


def _slot_usable(row) -> bool:
    return (
        row is not None
        and row.ura_question_set in (QUESTION_SET, CHOICE_QUESTION_SET)
        and row.ura_norm_version == NORM_VERSION
        and isinstance(row.ura_answer_hash, str)
        and _BCRYPT.match(row.ura_answer_hash) is not None
    )


def _registered_questions(rows: dict) -> tuple[str, ...]:
    if len(rows) == 1:
        question_id, row = next(iter(rows.items()))
        if question_id in QUESTION_IDS and _slot_usable(row) and row.ura_question_set == CHOICE_QUESTION_SET:
            return (question_id,)
    if set(rows) == set(QUESTION_IDS) and all(
        _slot_usable(rows[q]) and rows[q].ura_question_set == QUESTION_SET for q in QUESTION_IDS
    ):
        return QUESTION_IDS
    return ()


def is_registered(rows: dict) -> bool:
    return bool(_registered_questions(rows))


def check_answers(rows: dict, answers: dict[str, str]) -> bool:
    """세 슬롯을 모두 검증(bcrypt 정확히 3회)한 뒤 결과를 합친다. 쓸 수 없는 슬롯은 더미로 검증하고 실패로 센다."""
    required = _registered_questions(rows)
    ok = bool(required) and set(answers) == set(required)
    for question_id in QUESTION_IDS:
        row = rows.get(question_id)
        usable = question_id in required and _slot_usable(row)
        matched = verify_password(answers.get(question_id, ""), row.ura_answer_hash if usable else DUMMY_HASH)
        if question_id in required:
            ok = ok and usable and matched
    return ok


def hash_answers(answers: dict[str, str]) -> dict[str, str]:
    return {question_id: hash_password(answers[question_id]) for question_id in sorted(answers)}


def _is_user_id_unique_violation(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    if getattr(orig, "sqlstate", None) != "23505":
        return False
    diag = getattr(orig, "diag", None)
    return getattr(diag, "table_name", None) == "user_member" and getattr(diag, "constraint_name", None) in USER_ID_UNIQUE_NAMES


def create_learner_with_answers(db: Session, body: LearnerSignup, client_ip: str = "unknown"):
    """학습자 가입과 복구 답 등록을 한 트랜잭션으로. 회원만 있고 답이 없는 상태를 남기지 않는다."""
    try:
        user_id = body.user_id
        email_required = settings.signup_email_verification_enabled
        if email_required:
            user_id = signup_email.require_code(db, user_id, body.email_code, client_ip)
            if user_repositories.email_id_exists(db, user_id):
                raise DuplicateUserId
        elif body.email_code is not None:
            raise signup_email.EmailSignupUnavailable

        password_hash = hash_password(body.user_pw)
        answer_hashes = hash_answers(answer_map(body.recovery_answers))
        if not email_required and user_repositories.get_by_user_id(db, user_id, active_only=False) is not None:
            raise DuplicateUserId
        user = user_repositories.create(
            db,
            user_id=user_id,
            user_pw=password_hash,
            user_name=body.user_name,
            user_birth=body.user_birth,
            user_un=body.user_un,
            user_sp=body.user_sp,
        )
        repositories.insert_answers(db, user.idx, answer_hashes)
        db.commit()
        return user
    except IntegrityError as exc:
        db.rollback()
        if _is_user_id_unique_violation(exc):
            raise DuplicateUserId from None
        raise
    except Exception:
        db.rollback()
        raise


def get_status(db: Session, session: object) -> RecoveryStatus:
    user = settings_service._authenticated(db, session)
    rows = repositories.answers_for(db, user.idx)
    required = _registered_questions(rows)
    return RecoveryStatus(
        registered=bool(required),
        question_set=CHOICE_QUESTION_SET if len(required) == 1 else QUESTION_SET,
        question_ids=list(required or QUESTION_IDS),
    )


def save_answers(db: Session, session: object, body: RecoveryQuestionsPut, client_ip: str) -> RecoveryStatus:
    """최초 등록과 변경이 같은 경로다. 실패하면 rollback 하고 기존 답은 그대로 남는다."""
    user = settings_service._authenticated(db, session)
    settings_service._check_limit(user.idx, client_ip)   # 정보 수정·비밀번호 변경과 같은 learner 5 / IP 30 예산
    answer_hashes = hash_answers(answer_map(body.answers))
    try:
        locked = settings_repositories.lock_active_member(db, user.idx)
        if locked is None:
            raise settings_service.NotAuthenticated
        if not verify_password(body.current_password, locked.user_pw):
            raise settings_service.BadCurrentPassword
        repositories.replace_answers(db, locked.idx, answer_hashes, format_updated_at())
        db.commit()
    except Exception:
        db.rollback()
        raise
    return RecoveryStatus(
        registered=True,
        question_set=CHOICE_QUESTION_SET if len(answer_hashes) == 1 else QUESTION_SET,
        question_ids=sorted(answer_hashes),
    )
