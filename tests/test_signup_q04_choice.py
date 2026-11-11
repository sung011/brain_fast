"""r4 회귀: 질문 Q04(내 보물 1호)는 선택형(v2)에서만 쓰이고, 기존 세 답(v1)은 Q01·Q02·Q03 그대로다. 임시 PG·합성 값만."""
import pytest
from sqlalchemy import text

from schemas.accountRecoverySchemas import CHOICE_QUESTION_IDS, CHOICE_QUESTION_SET, QUESTION_IDS, QUESTION_LABELS

from recovery_b_support import ORIGIN, PW, BcryptCounter, answers
from test_signup_email_verification import ADDRESS, env, payload, pg, send, tally  # noqa: F401

NOT_FOUND = {"ok": False, "detail": "입력한 정보를 확인하지 못했어요. 다시 확인하거나 관리자에게 문의해 주세요."}
Q04 = [{"question_id": "Q04", "answer": "합성 보물"}]


def identity():
    return {"user_id": ADDRESS, "user_name": "합성", "user_birth": "1999-09-09"}


def signup_q04(client, code):
    body = payload(ADDRESS, code)
    body["recovery_answers"] = Q04
    return client.post("/signup", json=body)


def test_q04_labels_and_ids():
    assert QUESTION_IDS == ("Q01", "Q02", "Q03")
    assert CHOICE_QUESTION_IDS == ("Q01", "Q02", "Q03", "Q04") and QUESTION_LABELS["Q04"] == "내 보물 1호"


def test_q04_signup_recovery_and_reset(env, pg, monkeypatch):
    client, _ = env
    res = signup_q04(client, send(env))
    assert res.status_code == 200
    with pg.connect() as conn:
        rows = conn.execute(text("SELECT ura_question_id, ura_question_set FROM user_recovery_answer")).all()
    assert rows == [("Q04", CHOICE_QUESTION_SET)]
    bc = BcryptCounter().install(monkeypatch)
    ok = client.post("/find-pw/verify", json=dict(identity(), answers=Q04))
    right_checks = bc.check
    bc.reset()
    wrong = client.post("/find-pw/verify", json=dict(identity(), answers=[{"question_id": "Q04", "answer": "다른 답"}]))
    wrong_checks = bc.check
    bc.reset()
    other_q = client.post("/find-pw/verify", json=dict(identity(), answers=[{"question_id": "Q01", "answer": "합성 보물"}]))
    other_checks = bc.check
    print(f"\nPROBE r4 q04 find ok={ok.status_code}/{right_checks} wrong={wrong.status_code}/{wrong_checks} other_q={other_q.status_code}/{other_checks}")
    assert (ok.status_code, wrong.status_code, other_q.status_code) == (200, 404, 404)
    assert right_checks == wrong_checks == other_checks == 3
    assert wrong.json() == other_q.json() == NOT_FOUND
    reset = client.post("/find-pw", json=dict(identity(), answers=Q04, user_pw="New-pass-9", user_pw_confirm="New-pass-9"))
    assert reset.status_code == 200


@pytest.mark.parametrize("ids", [("Q01", "Q02", "Q04"), ("Q02", "Q03", "Q04"), ("Q01", "Q03", "Q04")])
def test_three_answers_must_be_legacy_set(env, pg, ids):
    client, _ = env
    three = [{"question_id": q, "answer": f"SENTINEL-{q}"} for q in ids]
    find = client.post("/find-pw/verify", json=dict(identity(), answers=three))
    print(f"\nPROBE r4 three_with_q04 {ids} find={find.status_code} body={find.text[:120]}")
    assert find.status_code == 422 and "answers_count" in find.text and "SENTINEL" not in find.text


def test_two_answers_with_q04_rejected(env, pg):
    client, _ = env
    two = [{"question_id": "Q01", "answer": "a"}, {"question_id": "Q04", "answer": "b"}]
    assert client.post("/find-pw/verify", json=dict(identity(), answers=two)).status_code == 422


def test_put_switches_to_q04(env, pg):
    client, _ = env
    assert signup_q04(client, send(env)).status_code == 200
    with pg.connect() as conn:
        idx = conn.execute(text("SELECT idx FROM user_member")).scalar_one()
    client.post("/_set", json={"learner": {"idx": idx, "email_verified": True}})
    to_q02 = client.put("/me/recovery-questions", json={"current_password": PW, "answers": [{"question_id": "Q02", "answer": "새 별명"}]}, headers=ORIGIN)
    back = client.put("/me/recovery-questions", json={"current_password": PW, "answers": Q04}, headers=ORIGIN)
    three_bad = client.put("/me/recovery-questions", json={"current_password": PW, "answers": [{"question_id": q, "answer": "x"} for q in ("Q01", "Q02", "Q04")]}, headers=ORIGIN)
    legacy = client.put("/me/recovery-questions", json={"current_password": PW, "answers": answers()}, headers=ORIGIN)
    status = client.get("/me/recovery-questions").json()
    print(f"\nPROBE r4 put q02={to_q02.status_code} q04={back.status_code}:{back.json().get('question_ids')} three_bad={three_bad.status_code} legacy={legacy.status_code} status={status}")
    assert to_q02.status_code == 200 and back.status_code == 200 and back.json()["question_ids"] == ["Q04"]
    assert three_bad.status_code == 422 and legacy.status_code == 200
    assert status["question_set"] == "security_questions_v1" and status["question_ids"] == list(QUESTION_IDS)
    assert tally(pg)[1] == 3
