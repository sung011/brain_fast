"""임시 PostgreSQL과 합성 편지함으로 가입 API를 확인한다."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import settings
from recovery_b_support import ROOT, PW, answers, app_engine, apply_ddl, client_for, start_pg, stop_pg
from schemas.userSchemas import UserLogin
from services import accountRecoveryServices, signupEmailServices as mail, userServices

ADDRESS = "long.sign.up.address+test@example.test"
OTHER = "other@example.test"


@pytest.fixture(scope="module")
def pg():
    tmp, server, engine = start_pg()
    try:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE user_member ALTER COLUMN user_id TYPE varchar(30)"))
        apply_ddl(engine, (ROOT / "docker/postgres/signup_email_code.sql").read_text(encoding="utf-8"))
        yield engine
    finally:
        stop_pg(tmp, server, engine)


@pytest.fixture
def env(pg, monkeypatch):
    monkeypatch.setattr(settings, "signup_email_verification_enabled", True)
    monkeypatch.setattr(settings, "signup_email_code_secret", SecretStr("synthetic-email-code-test-secret-2026"))
    monkeypatch.setattr(settings, "signup_smtp_host", "mail.example.test")
    monkeypatch.setattr(settings, "signup_smtp_from", "signup@example.test")
    for limiter in (mail.send_ip_limit, mail.send_total_limit, mail.verify_ip_limit):
        limiter.reset()
    with pg.begin() as connection:
        connection.execute(text("TRUNCATE signup_email_code, user_recovery_answer, user_member RESTART IDENTITY CASCADE"))
    sent = []

    class InboxSMTP:
        def __init__(self, host, port, **kwargs):
            assert host == "mail.example.test"
            self.tls = "context" in kwargs

        def starttls(self, *, context):
            assert context.check_hostname
            self.tls = True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send_message(self, message, *, to_addrs):
            assert self.tls and to_addrs == [str(message["To"])]
            sent.append((to_addrs[0], re.search(r"(?<![0-9])[0-9]{8}(?![0-9])", message.get_content()).group()))

    monkeypatch.setattr(mail.smtplib, "SMTP", InboxSMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", InboxSMTP)
    owner = app_engine(pg)
    try:
        with client_for(owner) as client:
            yield client, sent
    finally:
        owner.dispose()


def payload(address=ADDRESS, code=None):
    data = {
        "user_id": address, "user_pw": PW, "user_name": "합성",
        "user_birth": "1999-09-09", "user_un": "합성학교", "user_sp": "합성전공",
        "recovery_answers": answers()[:1],
    }
    if code is not None:
        data["email_code"] = code
    return data


def send(env, address=ADDRESS):
    client, sent = env
    res = client.post("/signup/email-code", json={"email": address})
    assert res.status_code == 202 and res.json() == {"ok": True}
    assert res.headers["Cache-Control"] == "no-store"
    assert sent[-1][1] not in res.text
    return sent[-1][1]


def tally(pg):
    with pg.connect() as connection:
        return tuple(connection.execute(text(
            "SELECT (SELECT count(*) FROM user_member), (SELECT count(*) FROM user_recovery_answer), "
            "(SELECT count(*) FROM signup_email_code)"
        )).one())


@pytest.mark.parametrize("ssl_mode", [False, True])
def test_delivered_code_enables_signup_and_login(env, pg, monkeypatch, ssl_mode):
    client, sent = env
    monkeypatch.setattr(settings, "signup_smtp_ssl", ssl_mode)
    assert client.post("/signup", json=payload()).status_code == 400
    assert tally(pg) == (0, 0, 0)
    code = send(env, ADDRESS.upper())
    assert sent[0][0] == ADDRESS and tally(pg) == (0, 0, 1)
    with pg.connect() as connection:
        row = connection.execute(text("SELECT email_key, code_digest FROM signup_email_code")).one()
        assert all(len(value) == 64 and code != value and ADDRESS not in value for value in row)
    assert client.post("/signup", json=payload(OTHER, code)).status_code == 400
    signup = client.post("/signup", json=payload(ADDRESS.upper(), code))
    assert signup.status_code == 200 and signup.json()["user_id"] == ADDRESS
    assert tally(pg) == (1, 1, 0)
    assert client.post("/signup", json=payload(ADDRESS, code)).status_code == 400
    with Session(client.engine) as db:
        user = userServices.login(db, UserLogin(user_id=ADDRESS.upper(), user_pw=PW))
        assert user is not None and user.idx == signup.json()["idx"]
    assert client.post("/find-pw/verify", json={
        "user_id": ADDRESS.upper(), "user_name": "합성", "user_birth": "1999-09-09", "answers": answers()[:1],
    }).status_code == 200


def test_cooldown_attempt_limit_and_expiry(env, pg):
    client, sent = env
    code = send(env)
    wrong = str((int(code) + 1) % 100_000_000).zfill(8)
    assert client.post("/signup/email-code", json={"email": ADDRESS.upper()}).status_code == 429
    assert len(sent) == 1
    for _ in range(mail.MAX_FAILURES):
        assert client.post("/signup", json=payload(ADDRESS, wrong)).status_code == 400
    assert client.post("/signup", json=payload(ADDRESS, code)).status_code == 400
    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    with pg.begin() as connection:
        connection.execute(text("UPDATE signup_email_code SET sent_at=:sent, expires_at=:expiry, failed_attempts=0"),
                           {"sent": expired - timedelta(minutes=10), "expiry": expired})
    assert client.post("/signup", json=payload(ADDRESS, code)).status_code == 400
    assert tally(pg) == (0, 0, 1)
    assert client.post("/signup", json=payload(ADDRESS, send(env))).status_code == 200


def test_signup_failure_preserves_code_and_retry(env, pg, monkeypatch):
    client, _ = env
    code = send(env)
    with monkeypatch.context() as patch:
        def fail(*_):
            raise RuntimeError("synthetic DB failure")
        patch.setattr(accountRecoveryServices.repositories, "insert_answers", fail)
        response = client.post("/signup", json=payload(ADDRESS, code))
        assert response.status_code == 500
        assert code not in response.text and PW not in response.text
    assert tally(pg) == (0, 0, 1)
    assert client.post("/signup", json=payload(ADDRESS, code)).status_code == 200
    assert tally(pg) == (1, 1, 0)


def test_concurrent_signup_consumes_code_once(env, pg):
    client, _ = env
    code = send(env)
    with ThreadPoolExecutor(2) as pool:
        result = list(pool.map(lambda _: client.post("/signup", json=payload(ADDRESS, code)).status_code, range(2)))
    assert sorted(result) == [200, 400]
    assert tally(pg) == (1, 1, 0)


def test_mail_failures_and_maintenance_are_fixed(env, pg, monkeypatch):
    client, _ = env
    class BadSMTP:
        def __init__(self, *_args, **_kwargs):
            raise OSError("sensitive-smtp-error")
    invalid = client.post("/signup/email-code", json={"email": "not-an-email", "sensitive-unknown-key": "test"})
    assert invalid.status_code == 422 and "sensitive-unknown-key" not in invalid.text
    with monkeypatch.context() as failing:
        failing.setattr(mail.smtplib, "SMTP", BadSMTP)
        response = client.post("/signup/email-code", json={"email": ADDRESS})
    assert response.status_code == 503 and "sensitive-smtp-error" not in response.text
    assert tally(pg) == (0, 0, 0)
    assert env[1] == []
    send(env)
    assert tally(pg) == (0, 0, 1) and len(env[1]) == 1
    assert client.post("/signup/email-code", json={"email": ADDRESS}).status_code == 429
    monkeypatch.setenv("ACCOUNT_RECOVERY_MAINTENANCE", "1")
    opened = client.app_ref.state.db_opens
    assert client.post("/signup/email-code", json={"email": OTHER}).status_code == 503
    assert client.post("/signup", json=payload(OTHER)).status_code == 503
    assert client.app_ref.state.db_opens == opened


@pytest.mark.parametrize("repeat_code", [False, True])
def test_failed_send_does_not_discard_a_later_issue(env, pg, monkeypatch, repeat_code):
    client, sent = env
    original_send = mail._send
    attempted = iter([12345678, 12345678 if repeat_code else 87654321])
    monkeypatch.setattr(mail.secrets, "randbelow", lambda _: next(attempted))

    def delayed_failure(email, code):
        # 발송 중 별도 연결이 이 행을 즉시 갱신할 수 있어야 한다.
        with pg.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '1s'"))
            conn.execute(text("UPDATE signup_email_code SET sent_at = sent_at - interval '61 seconds'"))
        monkeypatch.setattr(mail, "_send", original_send)
        with Session(client.engine) as db:
            mail.send_code(db, email, "synthetic-second-sender")
        raise mail.EmailSignupUnavailable

    monkeypatch.setattr(mail, "_send", delayed_failure)
    assert client.post("/signup/email-code", json={"email": ADDRESS}).status_code == 503
    assert len(sent) == 1 and tally(pg) == (0, 0, 1)
    assert client.post("/signup", json=payload(ADDRESS, sent[0][1])).status_code == 200
    assert tally(pg) == (1, 1, 0)


def test_existing_id_and_role_permissions(env, pg, monkeypatch):
    client, _ = env
    monkeypatch.setattr(settings, "signup_email_verification_enabled", False)
    legacy_id = "Legacy@Example.test"
    legacy = payload(legacy_id)
    legacy["recovery_answers"] = answers()
    assert client.post("/signup", json=legacy).status_code == 200
    with Session(client.engine) as db:
        assert userServices.login(db, UserLogin(user_id=legacy_id, user_pw=PW)).user_id == legacy_id
    monkeypatch.setattr(settings, "signup_email_verification_enabled", True)
    assert client.post("/signup", json=payload(legacy_id.lower(), send(env, legacy_id))).status_code == 409
    assert tally(pg)[:2] == (1, 3)
    with pg.connect() as connection:
        assert connection.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid='signup_email_code'::regclass")).scalar_one()
        assert connection.execute(text("SELECT count(*) FROM pg_policy WHERE polrelid='signup_email_code'::regclass")).scalar_one() == 0
        for role in ("anon", "authenticated"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not connection.execute(text("SELECT has_table_privilege(:role,'signup_email_code',:priv)"),
                                              {"role": role, "priv": privilege}).scalar_one()

