"""r3 회귀: 질문 하나 선택(v2)·기존 세 답(v1) 복구 호환. 임시 PG·합성 값만."""
import pytest
from sqlalchemy import text

from schemas.accountRecoverySchemas import CHOICE_QUESTION_SET, QUESTION_SET
from services import accountServices

from recovery_b_support import ANS, ORIGIN, PW, BcryptCounter, answers, client_for, identity, make_hashes, seed, start_pg, stop_pg
from utils.password import hash_password

NOT_FOUND = {"ok": False, "detail": "입력한 정보를 확인하지 못했어요. 다시 확인하거나 관리자에게 문의해 주세요."}


@pytest.fixture(scope="module")
def pg():
    tmp, server, engine = start_pg()
    yield engine
    stop_pg(tmp, server, engine)


@pytest.fixture(scope="module")
def hashes():
    return make_hashes()


@pytest.fixture
def env(pg, hashes, monkeypatch):
    seed(pg, hash_password(PW), hashes)
    # 회원 2(b@)를 새 선택형(Q02 하나, v2)으로 바꾼다
    with pg.begin() as conn:
        conn.execute(text("DELETE FROM user_recovery_answer WHERE ura_u_idx=2 AND ura_question_id<>'Q02'"))
        conn.execute(text("UPDATE user_recovery_answer SET ura_question_set=:s WHERE ura_u_idx=2"), {"s": CHOICE_QUESTION_SET})
    with client_for(pg) as client:
        yield client


def rows_of(pg, idx):
    with pg.connect() as conn:
        return conn.execute(text("SELECT ura_question_id, ura_question_set FROM user_recovery_answer WHERE ura_u_idx=:u ORDER BY 1"), {"u": idx}).all()


def find(env, who, ans):
    accountServices.ip_limiter.reset()
    accountServices.identity_limiter.reset()
    return env.post("/find-pw/verify", json=dict(who, answers=ans))


A = identity("a@example.test")                                  # v1 세 답
B = dict(identity("b@example.test"), user_name="비")             # v2 Q02 하나
NOBODY = identity("nobody@example.test")
NOANS = identity("noans@example.test")
ONE = lambda q, v=None: [{"question_id": q, "answer": v if v is not None else ANS[q]}]  # noqa: E731

CASES = {
    "v1_three_right": (A, answers(), 200),
    "v1_one_right": (A, ONE("Q01"), 404),
    "v1_other_one_right": (A, ONE("Q03"), 404),
    "v2_right": (B, ONE("Q02"), 200),
    "v2_wrong_answer": (B, ONE("Q02", "틀린답"), 404),
    "v2_right_answer_wrong_question": (B, [{"question_id": "Q01", "answer": ANS["Q02"]}], 404),
    "v2_given_three": (B, answers(), 404),
    "unknown_user": (NOBODY, ONE("Q01"), 404),
    "unregistered_user": (NOANS, ONE("Q01"), 404),
}


@pytest.mark.parametrize("case", list(CASES))
def test_r3_find_pw_uniform_bcrypt_and_body(env, monkeypatch, case):
    who, ans, status = CASES[case]
    bc = BcryptCounter().install(monkeypatch)
    res = find(env, who, ans)
    print(f"\nPROBE r3 find_pw {case} status={res.status_code} checkpw={bc.check}")
    assert res.status_code == status and bc.check == 3
    if status == 404:
        assert res.json() == NOT_FOUND


@pytest.mark.parametrize("layout,expected", [
    ("v1_single_row", False),       # 행 1개인데 v1 표시 → 미등록
    ("v2_three_rows", False),       # 행 3개인데 v2 표시 → 미등록
    ("v1_two_rows", False),         # 행 2개 → 미등록
    ("mixed_three", False),         # 3개 중 하나만 v2 → 미등록
])
def test_r3_malformed_rows_not_registered(env, pg, layout, expected):
    with pg.begin() as conn:
        if layout == "v1_single_row":
            conn.execute(text("DELETE FROM user_recovery_answer WHERE ura_u_idx=1 AND ura_question_id<>'Q01'"))
        elif layout == "v2_three_rows":
            conn.execute(text("UPDATE user_recovery_answer SET ura_question_set=:s WHERE ura_u_idx=1"), {"s": CHOICE_QUESTION_SET})
        elif layout == "v1_two_rows":
            conn.execute(text("DELETE FROM user_recovery_answer WHERE ura_u_idx=1 AND ura_question_id='Q03'"))
        else:
            conn.execute(text("UPDATE user_recovery_answer SET ura_question_set=:s WHERE ura_u_idx=1 AND ura_question_id='Q02'"), {"s": CHOICE_QUESTION_SET})
    env.login(1)
    status = env.get("/me/recovery-questions").json()
    full = find(env, A, answers()).status_code
    single = find(env, A, ONE("Q01")).status_code
    print(f"\nPROBE r3 malformed {layout} registered={status['registered']} find3={full} find1={single}")
    assert status["registered"] is expected and full == 404 and single == 404


def test_r3_status_reports_choice(env):
    env.login(2)
    st = env.get("/me/recovery-questions").json()
    env.login(1)
    legacy = env.get("/me/recovery-questions").json()
    print(f"\nPROBE r3 status v2={st} v1={legacy}")
    assert st == {"ok": True, "registered": True, "question_set": CHOICE_QUESTION_SET, "question_ids": ["Q02"]}
    assert legacy == {"ok": True, "registered": True, "question_set": QUESTION_SET, "question_ids": ["Q01", "Q02", "Q03"]}


def test_r3_put_switches_v1_to_v2_and_back(env, pg):
    env.login(1)
    two = env.put("/me/recovery-questions", json={"current_password": PW, "answers": answers()[:2]}, headers=ORIGIN)
    after_two = rows_of(pg, 1)
    one = env.put("/me/recovery-questions", json={"current_password": PW, "answers": ONE("Q03", "새 답")}, headers=ORIGIN)
    after_one = rows_of(pg, 1)
    old_three = find(env, A, answers()).status_code
    new_one = find(env, A, ONE("Q03", "새 답")).status_code
    env.login(1)
    back = env.put("/me/recovery-questions", json={"current_password": PW, "answers": answers()}, headers=ORIGIN)
    after_back = rows_of(pg, 1)
    print(f"\nPROBE r3 put two={two.status_code} rows={len(after_two)} one={one.status_code} rows={after_one} old3={old_three} new1={new_one} back={back.status_code} rows={len(after_back)}")
    assert two.status_code == 422 and "answers_count" in two.text and len(after_two) == 3
    assert one.status_code == 200 and one.json()["question_set"] == CHOICE_QUESTION_SET
    assert after_one == [("Q03", CHOICE_QUESTION_SET)] and old_three == 404 and new_one == 200
    assert back.status_code == 200 and after_back == [(q, QUESTION_SET) for q in ("Q01", "Q02", "Q03")]


def test_r3_find_pw_reset_v2_then_login(env, pg):
    reset = env.post("/find-pw", json=dict(B, answers=ONE("Q02"), user_pw="New-pass-9", user_pw_confirm="New-pass-9"))
    print(f"\nPROBE r3 reset_v2 status={reset.status_code}")
    assert reset.status_code == 200
    assert rows_of(pg, 2) == [("Q02", CHOICE_QUESTION_SET)]
