"""인증번호 확인 버튼과 질문 하나 선택의 서버 계약."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from config import settings
from schemas.accountRecoverySchemas import CHOICE_QUESTION_SET
from test_signup_email_verification import ADDRESS, env, payload, pg, send, tally
from recovery_b_support import PW, answers


def verify(client, code, address=ADDRESS):
    return client.post("/signup/email-code/verify", json={"email": address, "email_code": code})


def test_verify_then_signup_consumes_once(env, pg):
    client, _ = env
    code = send(env)
    response = verify(client, code)
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert response.headers["cache-control"] == "no-store"
    assert tally(pg) == (0, 0, 1)
    assert verify(client, code).status_code == 200
    assert client.post("/signup", json=payload(ADDRESS, code)).status_code == 200
    assert tally(pg) == (1, 1, 0)
    assert verify(client, code).status_code == 400


def test_verification_does_not_bypass_final_expiry_or_edited_code(env, pg):
    client, _ = env
    code = send(env)
    assert verify(client, code).status_code == 200
    wrong = str((int(code) + 1) % 100_000_000).zfill(8)
    assert client.post("/signup", json=payload(ADDRESS, wrong)).status_code == 400
    now = datetime.now(timezone.utc)
    with pg.begin() as connection:
        connection.execute(text("UPDATE signup_email_code SET sent_at=:sent,expires_at=:expiry"),
                           {"sent": now - timedelta(minutes=20), "expiry": now - timedelta(minutes=10)})
    assert client.post("/signup", json=payload(ADDRESS, code)).status_code == 400
    assert tally(pg)[:2] == (0, 0)


def test_verify_and_signup_share_wrong_attempts(env, pg):
    client, _ = env
    code = send(env)
    wrong = str((int(code) + 1) % 100_000_000).zfill(8)
    for _ in range(5):
        assert verify(client, wrong).status_code == 400
    assert verify(client, code).status_code == 400
    assert client.post("/signup", json=payload(ADDRESS, code)).status_code == 400
    assert tally(pg)[:2] == (0, 0)


def test_verify_strict_input_and_unavailable(env, monkeypatch):
    client, _ = env
    res = client.post("/signup/email-code/verify", json={"email": ADDRESS, "email_code": "sentinel-invalid-code", "private-key": "private"})
    assert res.status_code == 422 and "sentinel-invalid-code" not in res.text and "private-key" not in res.text
    monkeypatch.setattr(settings, "signup_email_verification_enabled", False)
    assert verify(client, "12345678").status_code == 503
    monkeypatch.setenv("ACCOUNT_RECOVERY_MAINTENANCE", "1")
    opened = client.app_ref.state.db_opens
    assert verify(client, "12345678").status_code == 503
    assert client.app_ref.state.db_opens == opened


@pytest.mark.parametrize("question", ["Q01", "Q02", "Q03"])
def test_selected_question_signup_and_password_recovery(env, pg, question):
    client, _ = env
    code = send(env)
    chosen = [{"question_id": question, "answer": "합성 선택 답"}]
    body = payload(ADDRESS, code)
    body["recovery_answers"] = chosen
    signup = client.post("/signup", json=body)
    assert signup.status_code == 200
    idx = signup.json()["idx"]
    with pg.connect() as conn:
        rows = conn.execute(text("SELECT ura_question_id,ura_question_set,ura_answer_hash FROM user_recovery_answer WHERE ura_u_idx=:idx"), {"idx": idx}).all()
    assert len(rows) == 1 and rows[0][0] == question and rows[0][1] == CHOICE_QUESTION_SET
    assert rows[0][2] != "합성 선택 답"
    identity = {"user_id": ADDRESS, "user_name": "합성", "user_birth": "1999-09-09", "answers": chosen}
    assert client.post("/find-pw/verify", json=identity).status_code == 200
    other = "Q02" if question != "Q02" else "Q01"
    assert client.post("/find-pw/verify", json={**identity, "answers": [{"question_id": other, "answer": "합성 선택 답"}]}).status_code == 404
    reset = {**identity, "user_pw": PW + "-new", "user_pw_confirm": PW + "-new"}
    assert client.post("/find-pw", json=reset).status_code == 200


def test_new_signup_requires_one_and_legacy_three_stays_three(env, pg, monkeypatch):
    client, _ = env
    code = send(env)
    for submitted in ([], answers()[:2], answers()):
        body = payload(ADDRESS, code)
        body["recovery_answers"] = submitted
        assert client.post("/signup", json=body).status_code == 422
    assert tally(pg) == (0, 0, 1)
    monkeypatch.setattr(settings, "signup_email_verification_enabled", False)
    legacy = payload("legacy@example.test")
    legacy["recovery_answers"] = answers()
    assert client.post("/signup", json=legacy).status_code == 200
    identity = {"user_id": legacy["user_id"], "user_name": "합성", "user_birth": "1999-09-09"}
    assert client.post("/find-pw/verify", json={**identity, "answers": answers()[:1]}).status_code == 404
    assert client.post("/find-pw/verify", json={**identity, "answers": answers()}).status_code == 200
