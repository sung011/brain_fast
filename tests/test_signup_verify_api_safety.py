"""r3 회귀: 인증 확인 API. 임시 PG·합성 편지함만."""
from sqlalchemy import text

from services import signupEmailServices as mail

from test_signup_email_verification import ADDRESS, OTHER, env, payload, pg, send, tally  # noqa: F401


def verify(client, code, address=ADDRESS):
    return client.post("/signup/email-code/verify", json={"email": address, "email_code": code})


def nowait_lock(pg):
    with pg.connect() as conn:
        conn.execute(text("SET lock_timeout = '200ms'"))
        return conn.execute(text("SELECT count(*) FROM (SELECT 1 FROM signup_email_code FOR UPDATE NOWAIT) s")).scalar_one()


def test_r3_verify_releases_lock_and_keeps_code(env, pg):
    client, _ = env
    code = send(env)
    wrong = str((int(code) + 1) % 100_000_000).zfill(8)
    ok = verify(client, code)
    after_ok = nowait_lock(pg)
    bad = verify(client, wrong)
    after_bad = nowait_lock(pg)
    with pg.connect() as conn:
        failed = conn.execute(text("SELECT failed_attempts FROM signup_email_code")).scalar_one()
    signup = client.post("/signup", json=payload(ADDRESS, code))
    print(f"\nPROBE r3 verify ok={ok.status_code} lock_free={after_ok} bad={bad.status_code} lock_free={after_bad} failed={failed} signup={signup.status_code}")
    assert (ok.status_code, bad.status_code, signup.status_code) == (200, 400, 200)
    assert after_ok == 1 and after_bad == 1 and failed == 1 and tally(pg)[2] == 0


def test_r3_verify_no_enumeration_and_no_echo(env, pg):
    client, _ = env
    code = send(env)
    wrong = str((int(code) + 1) % 100_000_000).zfill(8)
    never_sent = verify(client, "12345678", OTHER)
    wrong_code = verify(client, wrong)
    print(f"\nPROBE r3 verify never_sent={never_sent.status_code} wrong={wrong_code.status_code} same_body={never_sent.json() == wrong_code.json()}")
    assert never_sent.status_code == wrong_code.status_code == 400 and never_sent.json() == wrong_code.json()
    for res in (never_sent, wrong_code):
        assert ADDRESS not in res.text and OTHER not in res.text and wrong not in res.text and res.headers["cache-control"] == "no-store"


def test_r3_verify_ip_limit_shared_with_signup(env, pg):
    client, _ = env
    code = send(env)
    for _ in range(mail.verify_ip_limit.max_attempts):
        verify(client, "00000000", OTHER)
    blocked_verify = verify(client, code)
    blocked_signup = client.post("/signup", json=payload(ADDRESS, code))
    print(f"\nPROBE r3 verify_limit verify={blocked_verify.status_code} signup={blocked_signup.status_code}")
    assert blocked_verify.status_code == 429 and blocked_signup.status_code == 429 and tally(pg)[:2] == (0, 0)


def test_r3_signup_three_answers_rejected_without_echo(env, pg):
    client, _ = env
    code = send(env)
    body = payload(ADDRESS, code)
    body["recovery_answers"] = [{"question_id": q, "answer": f"SENTINEL-{q}"} for q in ("Q01", "Q02", "Q03")]
    res = client.post("/signup", json=body)
    print(f"\nPROBE r3 signup_three status={res.status_code} body={res.text[:160]}")
    assert res.status_code == 422 and "answers_count" in res.text and "SENTINEL" not in res.text and tally(pg) == (0, 0, 1)
