"""가입 + 비밀번호 찾기 질문 답 동시 등록(복구 B안 RB02·RB04, D2·D7) 시험. 임시 내장 PostgreSQL·합성 값만.

- 3답 성공 → 회원 1·답 3, 응답 키 4개 그대로, 답 insert 예외·commit 실패 → 회원 0·답 0
- 같은 아이디 동시 가입 → 1 성공·1 409(user_id unique 만), FK·CHECK 위반 → 고정 500·rollback
- 6필드(answers 없음) → 422, 해시·DB 0. 비반사(응답·헤더·로그)
- POST /signup 중복 등록 없음, GET /signup·관리자 create_user 그대로
"""
import threading

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from models.userRecoveryAnswerModel import UserRecoveryAnswerModel
from repositories import accountRecoveryRepositories as recovery_repo
from repositories import userRepositories as user_repo
from routers import accountSafeErrors as safe
from routers import user as user_module
from schemas.userSchemas import UserCreate
from services import accountRecoveryServices as recovery
from services import userServices
from utils.password import hash_password, verify_password

from recovery_b_support import BcryptCounter, PW, answers, capture_logs, client_for, counts, make_hashes, seed, start_pg, stop_pg

SENT = "SENTINEL-51gn"
SENT_KEY = "SENTINELKEY_77ab"


@pytest.fixture(scope="module")
def pg():
    tmp, server, engine = start_pg()
    yield engine
    stop_pg(tmp, server, engine)


@pytest.fixture(scope="module")
def seeds():
    return hash_password(PW), make_hashes()


@pytest.fixture
def env(pg, seeds, monkeypatch):
    monkeypatch.delenv(safe.MAINTENANCE_ENV, raising=False)
    seed(pg, *seeds)
    with client_for(pg) as client:
        yield client


@pytest.fixture
def bc(monkeypatch):
    return BcryptCounter().install(monkeypatch)


def body(**over):
    b = {"user_id": "new@example.test", "user_pw": "pass-1234", "user_name": "새회원", "user_birth": "2000-05-05",
         "user_un": "합성대학교", "user_sp": "의학과", "recovery_answers": answers()}
    b.update(over)
    return b


def member_count(engine, user_id):
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM user_member WHERE user_id=:u"), {"u": user_id}).scalar_one()


def test_signup_creates_member_and_three_answer_hashes(env, bc):
    bc.reset()
    res = env.post("/signup", json=body())
    assert res.status_code == 200, res.text
    data = res.json()
    assert set(data) == {"ok", "idx", "user_id", "user_name"} and data["ok"] is True and data["user_name"] == "새회원"
    assert res.headers["cache-control"] == "no-store"
    assert bc.hash == 4 and bc.check == 0          # 비밀번호 1 + 답 3, 트랜잭션 전에 만든다
    for secret in ("pass-1234", "합성별명", "$2b$"):
        assert secret not in res.text
    idx = data["idx"]
    with env.engine.connect() as conn:
        rows = conn.execute(text("SELECT ura_question_id, ura_answer_hash, ura_question_set, ura_norm_version FROM user_recovery_answer WHERE ura_u_idx=:u ORDER BY 1"),
                            {"u": idx}).all()
    assert [r[0] for r in rows] == ["Q01", "Q02", "Q03"]
    assert all(r[1].startswith("$2b$12$") and r[2] == "security_questions_v1" and r[3] == "answer_norm_v1" for r in rows)
    # 가입한 답으로 바로 비밀번호 찾기가 된다.
    res = env.post("/find-pw/verify", json={"user_id": "new@example.test", "user_name": "새회원", "user_birth": "2000-05-05", "answers": answers()})
    assert res.status_code == 200


def test_signup_normalizes_before_hash(env):
    raw = [{"question_id": "Q01", "answer": "  합성   초등학교 "}, {"question_id": "Q02", "answer": "합성별명"}, {"question_id": "Q03", "answer": "합성동"}]
    assert env.post("/signup", json=body(recovery_answers=raw)).status_code == 200
    res = env.post("/find-pw/verify", json={"user_id": "new@example.test", "user_name": "새회원", "user_birth": "2000-05-05", "answers": answers()})
    assert res.status_code == 200


def test_six_field_signup_is_422_before_hash_and_db(env, bc):
    six = body()
    six.pop("recovery_answers")
    bc.reset()
    n = len(env.statements)
    res = env.post("/signup", json=six)
    assert res.status_code == 422 and res.json() == {"ok": False, "detail": [{"loc": ["body", "recovery_answers"], "code": "missing"}]}
    assert bc.total == 0 and len(env.statements) == n
    assert member_count(env.engine, "new@example.test") == 0


@pytest.mark.parametrize("over,expected", [
    ({"user_birth": "2999-01-01"}, {"loc": ["body", "user_birth"], "code": "future_date"}),
    ({"user_birth": "20990101"}, {"loc": ["body", "user_birth"], "code": "future_date"}),
    ({"user_birth": "881332"}, {"loc": ["body", "user_birth"], "code": "invalid_value"}),
    ({"user_pw": "abc"}, {"loc": ["body", "user_pw"], "code": "too_short"}),
    ({"user_id": "   "}, {"loc": ["body", "user_id"], "code": "empty"}),
    ({"user_name": "가" * 11}, {"loc": ["body", "user_name"], "code": "too_long"}),
    ({"user_un": 5}, {"loc": ["body", "user_un"], "code": "invalid_type"}),
    ({SENT_KEY: "x"}, {"loc": ["body"], "code": "not_allowed_field"}),
])
def test_signup_422_codes_fixed(env, over, expected):
    res = env.post("/signup", json=body(**over))
    assert res.status_code == 422 and expected in res.json()["detail"], res.text
    assert SENT_KEY not in res.text


def test_signup_under_14_code(env):
    from datetime import date
    today = date.today()
    res = env.post("/signup", json=body(user_birth=f"{today.year - 10:04d}-01-01"))
    assert {"loc": ["body", "user_birth"], "code": "under_age"} in res.json()["detail"]


def test_duplicate_id_is_409_same_shape(env):
    assert env.post("/signup", json=body()).status_code == 200
    res = env.post("/signup", json=body(recovery_answers=answers({"Q01": "a", "Q02": "b", "Q03": "c"})))
    assert res.status_code == 409 and res.json() == {"ok": False, "error_type": "http_error", "detail": {"message": "이미 사용 중인 아이디입니다."}}
    assert res.headers["cache-control"] == "no-store"
    # 삭제 회원의 아이디도 409(기존 규칙)
    assert env.post("/signup", json=body(user_id="gone@example.test")).status_code == 409


def test_concurrent_same_id_one_success_one_409(env, monkeypatch):
    barrier = threading.Barrier(2, timeout=20)
    orig = user_repo.get_by_user_id

    def racing_lookup(db, user_id, **kw):
        found = orig(db, user_id, **kw)
        barrier.wait()            # 두 요청 모두 '없음'을 본 뒤 동시에 insert → DB unique 로 판정
        return found

    monkeypatch.setattr(user_repo, "get_by_user_id", racing_lookup)
    results = []

    def go():
        results.append(env.post("/signup", json=body()).status_code)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert sorted(results) == [200, 409]
    m, _ = counts(env.engine)
    assert member_count(env.engine, "new@example.test") == 1
    with env.engine.connect() as conn:
        idx = conn.execute(text("SELECT idx FROM user_member WHERE user_id='new@example.test'")).scalar_one()
    assert counts(env.engine, idx)[1] == 3


def test_user_id_unique_constraint_name_matches_service(pg):
    with pg.connect() as conn:
        names = set(conn.execute(text("SELECT conname FROM pg_constraint WHERE conrelid='user_member'::regclass AND contype='u'")).scalars())
    assert names == set(recovery.USER_ID_UNIQUE_NAMES)


@pytest.mark.parametrize("fault", ["insert", "commit", "check_violation", "fk_violation", "answer_unique"])
def test_failures_roll_back_everything_fixed_500(env, monkeypatch, fault, tmp_path):
    before = counts(env.engine)
    if fault == "insert":
        def boom(*a, **k):
            raise RuntimeError(f"insert {SENT}")
        monkeypatch.setattr(recovery_repo, "insert_answers", boom)
    elif fault == "commit":
        def failing_commit(self):
            raise OperationalError(f"COMMIT {SENT}", {"p": SENT}, Exception(SENT))
        monkeypatch.setattr(Session, "commit", failing_commit)
    else:
        def bad_insert(db, user_idx, hashes, updated_at=None):
            qid = "Q09" if fault == "check_violation" else "Q01"
            target = 999999 if fault == "fk_violation" else user_idx
            db.add(UserRecoveryAnswerModel(ura_u_idx=target, ura_question_id=qid, ura_answer_hash=hashes["Q01"]))
            if fault == "answer_unique":
                db.add(UserRecoveryAnswerModel(ura_u_idx=target, ura_question_id="Q01", ura_answer_hash=hashes["Q02"]))
            db.flush()
        monkeypatch.setattr(recovery_repo, "insert_answers", bad_insert)
    with capture_logs(tmp_path) as logs:
        res = env.post("/signup", json=body(user_pw=SENT + "pw", recovery_answers=answers({"Q01": SENT + "1", "Q02": SENT + "2", "Q03": SENT + "3"})))
    assert res.status_code == 500 and res.json() == {"ok": False, "detail": "서버 처리 중 문제가 생겼습니다."}, res.text
    assert res.headers["cache-control"] == "no-store"
    blob = res.text + str(dict(res.headers)) + " ".join(logs["memory"]) + logs["file"]
    assert SENT not in blob
    monkeypatch.undo()
    assert counts(env.engine) == before and member_count(env.engine, "new@example.test") == 0


def test_signup_no_reflection_for_bad_inputs(env, tmp_path):
    sent_answers = [{"question_id": q, "answer": f"{SENT}-{q}"} for q in ("Q01", "Q02", "Q03")]
    cases = [
        body(recovery_answers=[{"question_id": "Q01", "answer": {"k": SENT}}] + sent_answers[1:]),
        {k: v for k, v in body(recovery_answers=sent_answers, user_pw=SENT).items() if k != "user_id"},
        body(recovery_answers=SENT),
        dict(body(recovery_answers=sent_answers), **{SENT_KEY: SENT}),
        body(recovery_answers=[dict(sent_answers[0], **{SENT_KEY: SENT})] + sent_answers[1:]),
        body(recovery_answers=sent_answers[:2]),
        body(recovery_answers=sent_answers + sent_answers[:1]),
        body(recovery_answers=[sent_answers[0], sent_answers[0], sent_answers[1]]),
        body(user_pw=SENT * 10, recovery_answers=sent_answers),
        body(user_birth=SENT, recovery_answers=sent_answers),
    ]
    before = counts(env.engine)
    for case in cases:
        with capture_logs(tmp_path) as logs:
            res = env.post("/signup", json=case)
        assert res.status_code == 422, res.text
        blob = res.text + str(dict(res.headers)) + " ".join(logs["memory"]) + logs["file"]
        assert SENT not in blob and SENT_KEY not in blob
        assert res.headers["cache-control"] == "no-store"
    for raw in (b'{"user_pw": "' + SENT.encode(), SENT.encode()):
        with capture_logs(tmp_path) as logs:
            res = env.post("/signup", content=raw, headers={"content-type": "application/json"})
        assert res.status_code == 422 and res.json()["detail"] == [{"loc": ["body"], "code": "invalid_body"}], res.text
        assert SENT not in res.text + " ".join(logs["memory"]) + logs["file"]
    assert counts(env.engine) == before


# ── 라우트 등록(D7)·무관 경로 불변 ─────────────────────────────────────────
def _walk(routes):
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _walk(original.routes)
        else:
            yield route


def test_post_signup_registered_exactly_once_and_get_signup_kept():
    from main import app
    found = [(sorted(r.methods), r.path, type(r).__name__) for r in _walk(app.routes) if getattr(r, "path", None) == "/signup"]
    posts = [f for f in found if f[0] == ["POST"]]
    gets = [f for f in found if f[0] == ["GET"]]
    assert len(posts) == 1 and posts[0][2] == "AnswerSafeRoute"
    assert len(gets) == 1 and gets[0][2] == "APIRoute"
    find_pw = [(sorted(r.methods), type(r).__name__) for r in _walk(app.routes) if getattr(r, "path", None) in ("/find-pw", "/find-pw/verify")]
    assert find_pw == [(["POST"], "AnswerSafeRoute")] * 2
    others = {getattr(r, "path", None): type(r).__name__ for r in _walk(app.routes)}
    assert others["/find-id"] == "AccountRoute" and others["/logout"] == "AccountRoute" and others["/me/settings"] == "AccountRoute"
    assert not [r for r in _walk(user_module.router.routes) if getattr(r, "path", "").startswith("/admin")]


def test_admin_create_user_unchanged(env):
    with Session(env.engine) as db:
        user = userServices.create_user(db, UserCreate(user_id="admin-made@example.test", user_pw="pass-1234", user_name="관리생성",
                                                       user_birth="2000-01-01", user_un="u", user_sp="s"))
        assert user is not None and verify_password("pass-1234", user.user_pw)
        idx = user.idx
    assert counts(env.engine, idx)[1] == 0      # 관리자 가입은 답을 만들지 않는다(바꾸지 않음)
