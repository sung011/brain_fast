"""복구 B안 시험 (임시 내장 PostgreSQL·합성 회원만). 계획 FOLLOWUP2_RECOVERY_B_PLAN.md r2 6절, 구현 체크리스트 [해석] 항목.

묶음: 정규화(B02)·해시(B03)·비반사(RB01·RB02, verify·reset·PUT)·복구(RB03·RB05)·경합(RB05)·등록 PUT(RB07 서버)·유지보수(RB06)·스키마 누락.
가입 쪽은 tests/test_learner_signup_answers.py.
"""
import threading
import time
import unicodedata

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from models.userModel import UserModel
from repositories import accountRecoveryRepositories as recovery_repo
from repositories import accountRepositories as account_repo
from repositories import accountSettingsRepositories as settings_repo
from routers import accountSafeErrors as safe
from schemas.accountRecoverySchemas import NORM_MAX_BYTES, RecoveryQuestionsPut, normalize_answer
from schemas.accountSchemas import FindPw
from services import accountRecoveryServices as recovery
from services import accountServices
from utils.password import hash_password, verify_password

from recovery_b_support import (ANS, ORIGIN, PW, BcryptCounter, answer_hash_rows, answers, capture_logs, client_for, counts,
                                identity, make_hashes, seed, start_pg, stop_pg)

NOT_FOUND = {"ok": False, "detail": "입력한 정보를 확인하지 못했어요. 다시 확인하거나 관리자에게 문의해 주세요."}
SENT = "SENTINEL-7f3a"            # 응답·로그에 나오면 안 되는 합성 민감값
SENT_KEY = "SENTINELKEY_9c1d"      # 사용자가 보낸 알 수 없는 키 이름


@pytest.fixture(scope="module")
def pg():
    tmp, server, engine = start_pg()
    yield engine
    stop_pg(tmp, server, engine)


@pytest.fixture(scope="module")
def hashes():
    return make_hashes()


@pytest.fixture(scope="module")
def pw_hash():
    return hash_password(PW)


@pytest.fixture
def env(pg, hashes, pw_hash, monkeypatch):
    monkeypatch.delenv(safe.MAINTENANCE_ENV, raising=False)
    seed(pg, pw_hash, hashes)
    with client_for(pg) as client:
        yield client


@pytest.fixture
def bc(monkeypatch):
    return BcryptCounter().install(monkeypatch)


def verify(env, body=None, **over):
    b = body if body is not None else dict(identity(), answers=answers())
    b.update(over)
    return env.post("/find-pw/verify", json=b)


def reset(env, new="New-pass-9", ans=None, **over):
    b = dict(identity(), answers=ans or answers(), user_pw=new, user_pw_confirm=new)
    b.update(over)
    return env.post("/find-pw", json=b)


def put(env, ans=None, current=PW, headers=ORIGIN):
    return env.put("/me/recovery-questions", json={"current_password": current, "answers": ans or answers()}, headers=headers)


def pw_of(engine, idx=1):
    with Session(engine) as db:
        return db.get(UserModel, idx).user_pw


# ── 정규화 B02 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,norm", [
    ("  합성   초등학교 ", "합성 초등학교"),
    ("ABC School", "abc school"),
    ("서울 초등학교", "서울 초등학교"),          # NBSP 도 공백 하나로
    ("서울　　초등학교", "서울 초등학교"),     # 전각 공백
    (unicodedata.normalize("NFD", "한글동"), "한글동"),   # NFD → NFC
])
def test_normalize_equivalents(raw, norm):
    assert normalize_answer(raw) == norm


@pytest.mark.parametrize("a,b", [("서울 초등학교", "서울초등학교"), ("ＡＢＣ", "abc"), ("ÄBC", "äbc"), ("ⅰ", "i")])
def test_normalize_not_equivalent(a, b):
    # 공백 유무·전각·비ASCII 대문자·NFKC 호환문자는 같은 답으로 보지 않는다.
    assert normalize_answer(a) != normalize_answer(b)


@pytest.mark.parametrize("raw,etype", [
    ("", "answer_empty"), ("   ", "answer_empty"), ("a" * 257, "answer_too_long"), ("가" * 51, "answer_too_long"),
    ("가" * 25, "answer_too_long"),   # 25자 = 75바이트 > 72
    ("줄\n바꿈", "answer_control"), ("탭\t", "answer_control"), ("a b", "answer_control"), ("\x7f", "answer_control"),
    (123, "answer_type"), (None, "answer_type"), (["x"], "answer_type"),
])
def test_normalize_rejects(raw, etype):
    from pydantic_core import PydanticCustomError
    with pytest.raises(PydanticCustomError) as info:
        normalize_answer(raw)
    assert info.value.type == etype


def test_normalize_boundaries():
    assert normalize_answer("a" * 50) == "a" * 50
    assert normalize_answer(" " * 200 + "a" * 50) == "a" * 50          # 원문 256자 이내면 앞뒤 공백 정리 뒤 길이로 본다
    assert len(normalize_answer("가" * 24).encode()) == 72 == NORM_MAX_BYTES
    with pytest.raises(Exception):
        normalize_answer(" " * 207 + "a" * 50)                          # 원문 257자는 먼저 거절


def test_password_is_not_normalized(env):
    # 비밀번호에는 답 정규화를 적용하지 않는다(앞뒤 공백 유지).
    assert reset(env, new="  Spaced PW  ").status_code == 200
    h = pw_of(env.engine)
    assert verify_password("  Spaced PW  ", h) and not verify_password("spaced pw", h)


# ── 해시 B03 ───────────────────────────────────────────────────────────────
def test_same_answers_two_members_have_different_hashes_and_no_plaintext(env):
    env.login(4)
    assert put(env).status_code == 200
    a, b = answer_hash_rows(env.engine, 1), answer_hash_rows(env.engine, 4)
    assert set(a) == set(b) == {"Q01", "Q02", "Q03"}
    for q in a:
        assert a[q] != b[q] and a[q].startswith("$2b$12$") and b[q].startswith("$2b$12$")
    with env.engine.connect() as conn:
        dump = " ".join(str(v) for row in conn.execute(text("SELECT * FROM user_recovery_answer")).all() for v in row)
    for value in ANS.values():
        assert value not in dump
    assert verify(env, body=dict(identity("noans@example.test"), answers=answers())).status_code == 200


def test_backup_like_export_has_no_plaintext(env):
    # 백업 산출물(테이블 전체 COPY)에도 답 평문이 없다.
    raw = env.engine.raw_connection()
    try:
        with raw.cursor() as cur:
            with cur.copy("COPY user_recovery_answer TO STDOUT WITH CSV") as copy:
                data = b"".join(bytes(chunk) for chunk in copy).decode()
    finally:
        raw.close()
    assert data and all(v not in data for v in ANS.values())


def test_dummy_hash_is_valid_cost12_and_created_once(bc):
    assert recovery.DUMMY_HASH.startswith("$2b$12$") and len(recovery.DUMMY_HASH) == 60
    bc.reset()
    recovery.check_answers({}, ANS)
    assert bc.hash == 0 and bc.check == 3    # 요청마다 gensalt/hash 0, 검증 정확히 3회


# ── 복구 RB03·RB05 ──────────────────────────────────────────────────────────
def test_three_of_three_verify_and_reset_then_login(env, bc):
    bc.reset()
    res = verify(env)
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert bc.check == 3 and bc.hash == 0
    bc.reset()
    res = reset(env)
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    assert bc.check == 3 and bc.hash == 1
    assert verify_password("New-pass-9", pw_of(env.engine))


def _wrong(q):
    values = dict(ANS)
    values[q] = "틀린답"
    return answers(values)


@pytest.mark.parametrize("case", ["Q01", "Q02", "Q03", "unregistered", "deleted", "missing_id", "wrong_name", "version", "damaged", "two_rows"])
def test_failures_share_404_and_exactly_three_checks(env, bc, case):
    body = dict(identity(), answers=answers())
    if case in ("Q01", "Q02", "Q03"):
        body["answers"] = _wrong(case)
    elif case == "unregistered":
        body.update(identity("noans@example.test"))
    elif case == "deleted":
        body.update(identity("gone@example.test"))
    elif case == "missing_id":
        body.update(identity("nobody@example.test"))
    elif case == "wrong_name":
        body["user_name"] = "다른이름"
    else:
        with env.engine.begin() as conn:
            if case == "version":
                conn.execute(text("UPDATE user_recovery_answer SET ura_norm_version='answer_norm_v0' WHERE ura_u_idx=1 AND ura_question_id='Q02'"))
            elif case == "damaged":
                conn.execute(text("UPDATE user_recovery_answer SET ura_answer_hash='not-a-bcrypt-hash' WHERE ura_u_idx=1 AND ura_question_id='Q03'"))
            else:
                conn.execute(text("DELETE FROM user_recovery_answer WHERE ura_u_idx=1 AND ura_question_id='Q01'"))
    before = pw_of(env.engine, 1)
    bc.reset()
    res = env.post("/find-pw/verify", json=body)
    assert res.status_code == 404 and res.json() == NOT_FOUND and res.headers["cache-control"] == "no-store"
    assert bc.check == 3 and bc.hash == 0
    bc.reset()
    res = env.post("/find-pw", json=dict(body, user_pw="New-pass-9", user_pw_confirm="New-pass-9"))
    assert res.status_code == 404 and res.json() == NOT_FOUND
    assert bc.check == 3 and bc.hash == 1       # 새 비밀번호 해시 1 + 검증 3(성공 경로와 같은 수)
    assert pw_of(env.engine, 1) == before


def test_direct_reset_without_verify_is_allowed_and_rechecked(env):
    # verify 호출 이력은 권한 요건이 아니다. 완전한 신원+답+새 비밀번호면 reset 안에서 다시 검증하고 허용한다.
    assert reset(env, new="Direct-1").status_code == 200
    assert verify_password("Direct-1", pw_of(env.engine))


@pytest.mark.parametrize("drop", ["answers", "user_id", "user_name", "user_birth"])
def test_reset_missing_identity_or_answers_is_422_without_change(env, bc, drop):
    before = pw_of(env.engine)
    body = dict(identity(), answers=answers(), user_pw="New-pass-9", user_pw_confirm="New-pass-9")
    body.pop(drop)
    bc.reset()
    res = env.post("/find-pw", json=body)
    assert res.status_code == 422 and bc.total == 0
    assert pw_of(env.engine) == before


def test_client_verified_flag_does_not_bypass(env):
    res = env.post("/find-pw", json=dict(identity(), verified=True, user_pw="New-pass-9", user_pw_confirm="New-pass-9"))
    assert res.status_code == 422


def test_answers_changed_between_verify_and_reset_rejects_old(env):
    assert verify(env).status_code == 200
    env.login(1)
    new_values = {"Q01": "새 학교", "Q02": "새별명", "Q03": "새동네"}
    assert put(env, ans=answers(new_values)).status_code == 200
    assert reset(env).status_code == 404                      # 옛 답 거절
    assert reset(env, ans=answers(new_values)).status_code == 200


def test_two_valid_resets_both_succeed_in_order(env):
    assert reset(env, new="First-111").status_code == 200
    assert reset(env, new="Second-222").status_code == 200
    assert verify_password("Second-222", pw_of(env.engine))


def test_identity_limit_shared_and_429_does_no_bcrypt(env, bc):
    for i in range(3):
        assert verify(env, answers=_wrong("Q01")).status_code == 404
    for i in range(2):
        assert reset(env, ans=_wrong("Q02")).status_code == 404
    bc.reset()
    statements_before = len(env.statements)
    assert verify(env).status_code == 429
    assert reset(env).status_code == 429
    assert bc.total == 0 and len(env.statements) == statements_before   # 제한 초과는 bcrypt·DB 0
    assert env.post("/find-id", json={"user_name": "비", "user_birth": "1999-09-09"}).status_code == 200


def test_ip_limit_30_shared_with_find_id(env, bc):
    for i in range(30):
        env.post("/find-id", json={"user_name": f"n{i}", "user_birth": "1999-09-09"})
    bc.reset()
    assert verify(env).status_code == 429 and bc.total == 0


def test_mismatch_new_password_is_400_without_change(env):
    before = pw_of(env.engine)
    res = reset(env, user_pw="aaaa1", user_pw_confirm="bbbb2")
    assert res.status_code == 400 and res.headers["cache-control"] == "no-store"
    assert pw_of(env.engine) == before


# ── 비반사 RB01·RB02 (verify·reset·PUT) ────────────────────────────────────
def _payloads(kind):
    """(이름, 요청 kwargs) 목록. kind: verify|reset|put"""
    base = {"verify": dict(identity(), answers=answers()),
            "reset": dict(identity(), answers=answers(), user_pw=SENT + "-pw", user_pw_confirm=SENT + "-pw"),
            "put": {"current_password": SENT + "-cur", "answers": answers()}}[kind]
    sent_answers = [{"question_id": q, "answer": f"{SENT}-{q}"} for q in ("Q01", "Q02", "Q03")]

    def body(**over):
        b = dict(base)
        b["answers"] = [dict(a) for a in sent_answers]
        b.update(over)
        return b

    parent = "current_password" if kind == "put" else "user_id"
    cases = [
        ("normal", {"json": body()}),
        ("answer_type", {"json": body(answers=[{"question_id": "Q01", "answer": {"x": SENT}}, sent_answers[1], sent_answers[2]])}),
        ("answer_list_type", {"json": body(answers=[{"question_id": "Q01", "answer": [SENT]}, sent_answers[1], sent_answers[2]])}),
        ("parent_missing", {"json": {k: v for k, v in body().items() if k != parent}}),
        ("array_type", {"json": body(answers=SENT)}),
        ("array_object", {"json": body(answers={SENT_KEY: SENT})}),
        ("unknown_top_key", {"json": dict(body(), **{SENT_KEY: SENT})}),
        ("unknown_item_key", {"json": body(answers=[dict(sent_answers[0], **{SENT_KEY: SENT}), sent_answers[1], sent_answers[2]])}),
        ("two_answers", {"json": body(answers=sent_answers[:2])}),
        ("four_answers", {"json": body(answers=sent_answers + [{"question_id": "Q01", "answer": SENT}])}),
        ("dup_ids", {"json": body(answers=[sent_answers[0], sent_answers[0], sent_answers[2]])}),
        ("bad_qid", {"json": body(answers=[{"question_id": SENT, "answer": SENT}, sent_answers[1], sent_answers[2]])}),
        ("too_long", {"json": body(answers=[{"question_id": "Q01", "answer": SENT * 40}, sent_answers[1], sent_answers[2]])}),
        ("control", {"json": body(answers=[{"question_id": "Q01", "answer": SENT + "\n"}, sent_answers[1], sent_answers[2]])}),
        ("bad_json", {"content": ('{"answers": [{"answer": "' + SENT + '"').encode(), "headers": {"content-type": "application/json"}}),
        ("text_body", {"content": SENT.encode(), "headers": {"content-type": "text/plain"}}),
        ("not_object", {"json": [SENT]}),
    ]
    return cases


def _send(env, kind, kwargs):
    headers = dict(ORIGIN) if kind == "put" else {}
    headers.update(kwargs.pop("headers", {}))
    if kind == "verify":
        return env.post("/find-pw/verify", headers=headers, **kwargs)
    if kind == "reset":
        return env.post("/find-pw", headers=headers, **kwargs)
    return env.put("/me/recovery-questions", headers=headers, **kwargs)


def _assert_clean(res, logs, *extra):
    blob = res.text + " " + " ".join(f"{k}:{v}" for k, v in res.headers.items())
    for secret in (SENT, SENT_KEY) + extra:
        assert secret not in blob, (res.status_code, res.text)
        assert secret not in " ".join(logs["memory"]), "memory log leak"
        assert secret not in logs["file"], "file log leak"
    assert res.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("kind", ["verify", "reset", "put"])
def test_no_reflection_in_response_headers_or_logs(env, kind, tmp_path):
    if kind == "put":
        env.login(1)
    before = (counts(env.engine), pw_of(env.engine), answer_hash_rows(env.engine, 1))
    for name, kwargs in _payloads(kind):
        with capture_logs(tmp_path) as logs:
            res = _send(env, kind, dict(kwargs))
        _assert_clean(res, logs)
        if name != "normal":
            assert res.status_code == 422, (name, res.status_code, res.text)
            detail = res.json()["detail"]
            assert res.json()["ok"] is False and isinstance(detail, list) and detail
            for item in detail:
                assert set(item) == {"loc", "code"} and item["code"] in safe.ALL_CODES
    after = (counts(env.engine), pw_of(env.engine), answer_hash_rows(env.engine, 1))
    if kind == "put":
        assert after[0] == before[0] and after[1] == before[1]   # 잘못된 현재 비밀번호(normal) → 쓰기 0
    else:
        assert after == before


@pytest.mark.parametrize("kind,target", [("verify", "check"), ("reset", "check"), ("reset", "commit"), ("put", "replace"), ("put", "commit")])
def test_internal_errors_are_fixed_500_without_reflection(env, kind, target, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError(f"params {SENT} user_pw={SENT}")

    if target == "check":
        monkeypatch.setattr(recovery, "check_answers", boom)
    elif target == "replace":
        monkeypatch.setattr(recovery_repo, "replace_answers", boom)
    else:
        def failing_commit(self):
            raise OperationalError(f"COMMIT {SENT}", {"p": SENT}, Exception(SENT))
        monkeypatch.setattr(Session, "commit", failing_commit)
    if kind == "put":
        env.login(1)
    before = (counts(env.engine), pw_of(env.engine), answer_hash_rows(env.engine, 1))
    body = {"verify": dict(identity(), answers=answers()),
            "reset": dict(identity(), answers=answers(), user_pw=SENT, user_pw_confirm=SENT),
            "put": {"current_password": PW, "answers": [{"question_id": q, "answer": f"{SENT}-{q}"} for q in ("Q01", "Q02", "Q03")]}}[kind]
    with capture_logs(tmp_path) as logs:
        res = _send(env, kind, {"json": body})
    assert res.status_code == 500 and res.json() == {"ok": False, "detail": "서버 처리 중 문제가 생겼습니다."}
    _assert_clean(res, logs)
    assert any("status=500" in line and "error_type=" in line for line in logs["memory"])
    monkeypatch.undo()
    assert (counts(env.engine), pw_of(env.engine), answer_hash_rows(env.engine, 1)) == before   # 부분 저장 0


def test_422_mapping_is_fixed(env):
    def detail(body):
        res = env.post("/find-pw/verify", json=body)
        assert res.status_code == 422
        return res.json()["detail"]

    full = dict(identity(), answers=answers())
    assert {"loc": ["body", "answers"], "code": "missing"} in detail({k: v for k, v in full.items() if k != "answers"})
    assert detail(dict(full, answers=answers()[:2])) == [{"loc": ["body", "answers"], "code": "answers_count"}]
    assert detail(dict(full, answers=[answers()[0]] * 3)) == [{"loc": ["body", "answers"], "code": "answers_duplicate"}]
    assert detail(dict(full, answers=[{"question_id": "Q01", "answer": 5}] + answers()[1:])) == [{"loc": ["body", "answers", 0, "answer"], "code": "invalid_type"}]
    assert detail(dict(full, answers=[{"question_id": "Q01", "answer": ""}] + answers()[1:])) == [{"loc": ["body", "answers", 0, "answer"], "code": "empty"}]
    assert detail(dict(full, answers=[{"question_id": "Q01", "answer": "x" * 51}] + answers()[1:])) == [{"loc": ["body", "answers", 0, "answer"], "code": "too_long"}]
    assert detail(dict(full, answers=[{"question_id": "Q09", "answer": "x"}] + answers()[1:])) == [{"loc": ["body", "answers", 0, "question_id"], "code": "invalid_value"}]
    assert detail(dict(full, **{SENT_KEY: 1})) == [{"loc": ["body"], "code": "not_allowed_field"}]
    assert detail(dict(full, answers=answers() + [answers()[0]])) == [{"loc": ["body", "answers"], "code": "answers_count"}]


def test_safe_loc_checks_pieces_by_exact_type():
    allowed = safe.VERIFY_LOCS
    assert safe.safe_loc(("body", "answers", 0, "answer"), allowed) == ("body", "answers", 0, "answer")
    assert safe.safe_loc(("body", "answers", True, "answer"), allowed) == ("body",)      # bool 은 int 로 보지 않는다
    assert safe.safe_loc(("body", "answers", 1.0), allowed) == ("body",)
    assert safe.safe_loc(("body", "answers", 3, "answer"), allowed) == ("body",)         # 범위 밖 번호
    assert safe.safe_loc(("body", "answers", 0, SENT_KEY), allowed) == ("body",)
    assert safe.safe_loc(("body", "answers.0"), allowed) == ("body",)                   # 이어 붙인 문자열 경로는 허용 아님
    assert safe.safe_loc(("query", "user_id"), allowed) == ("body",)
    assert safe.safe_loc("body.answers", allowed) == ("body",)
    assert safe.safe_code(object(), ("body",)) == "invalid_value"
    assert safe.safe_code("some_new_pydantic_type", ("body",)) == "invalid_value"


# ── 등록 PUT (RB07 서버 쪽) ─────────────────────────────────────────────────
def test_unregistered_member_registers_after_login(env, bc):
    env.login(4)
    res = env.get("/me/recovery-questions")
    assert res.status_code == 200 and res.json() == {"ok": True, "registered": False, "question_set": "security_questions_v1", "question_ids": ["Q01", "Q02", "Q03"]}
    assert res.headers["cache-control"] == "no-store"
    bc.reset()
    res = put(env)
    assert res.status_code == 200 and res.json()["registered"] is True and res.headers["cache-control"] == "no-store"
    assert bc.hash == 3 and bc.check == 1
    assert env.get("/me/recovery-questions").json()["registered"] is True
    assert verify(env, body=dict(identity("noans@example.test"), answers=answers())).status_code == 200
    for key in ("hash", "answer", "$2b$"):
        assert key not in env.get("/me/recovery-questions").text


def test_wrong_current_password_is_400_and_no_write(env):
    env.login(1)
    before = answer_hash_rows(env.engine, 1)
    res = put(env, current="wrong-pass", ans=answers({"Q01": "x", "Q02": "y", "Q03": "z"}))
    assert res.status_code == 400 and res.json()["detail"] == "현재 비밀번호가 맞지 않습니다."
    assert answer_hash_rows(env.engine, 1) == before


@pytest.mark.parametrize("headers", [{}, {"Origin": "http://evil.example"}, {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
                                     {"Origin": "http://testserver/path"}, {"Origin": "http://user@testserver"}])
def test_put_requires_strict_origin(env, headers, bc):
    env.login(1)
    before = answer_hash_rows(env.engine, 1)
    bc.reset()
    res = put(env, headers=headers)
    assert res.status_code == 403 and res.headers["cache-control"] == "no-store" and bc.total == 0
    assert answer_hash_rows(env.engine, 1) == before


@pytest.mark.parametrize("session,status", [(None, 401), ({"learner": {"idx": 1, "email_verified": False}}, 403),
                                            ({"learner": {"idx": 3, "email_verified": True}}, 401), ({"user": {"idx": 1}}, 401),
                                            ({"learner": {"idx": True, "email_verified": True}}, 401), ({"guest": "x"}, 401)])
def test_get_put_auth(env, session, status, bc):
    if session is not None:
        env.post("/_set", json=session)
    bc.reset()
    assert env.get("/me/recovery-questions").status_code == status
    assert put(env).status_code == status and bc.total == 0


def test_get_rejects_identity_query(env):
    env.login(1)
    assert env.get("/me/recovery-questions?user_idx=2").status_code == 400


def test_put_failure_keeps_old_answers(env, monkeypatch):
    env.login(1)
    before = answer_hash_rows(env.engine, 1)
    orig_insert = recovery_repo.insert_answers

    def half_insert(db, user_idx, hashes, updated_at=None):
        orig_insert(db, user_idx, {"Q01": hashes["Q01"]}, updated_at)   # 1행만 넣고 실패
        raise RuntimeError("mid-insert failure")

    monkeypatch.setattr(recovery_repo, "insert_answers", half_insert)
    assert put(env, ans=answers({"Q01": "x", "Q02": "y", "Q03": "z"})).status_code == 500
    monkeypatch.undo()
    assert answer_hash_rows(env.engine, 1) == before


def test_put_budget_shared_with_profile_and_429_no_bcrypt(env, bc):
    env.login(1)
    version = env.get("/me/settings").json()["profile_version"]
    for _ in range(3):
        assert env.patch("/me/profile", json={"user_name": "a", "user_un": "b", "user_sp": "c", "current_password": "wrong", "expected_version": version},
                         headers=ORIGIN).status_code == 400
    assert put(env, current="wrong").status_code == 400
    assert put(env, current="wrong").status_code == 400
    bc.reset()
    assert put(env).status_code == 429 and bc.total == 0
    assert env.post("/me/password", json={"current_password": PW, "new_password": "zzzz9", "new_password_confirm": "zzzz9"}, headers=ORIGIN).status_code == 429


# ── 경합 RB05 (실제 임시 PG 두 연결, 미리 읽은 객체) ────────────────────────
def test_isolation_level_is_read_committed(pg):
    with pg.connect() as conn:
        assert conn.execute(text("SHOW transaction_isolation")).scalar_one() == "read committed"


def test_put_commits_while_reset_waits_old_answers_rejected(env):
    engine = env.engine
    new_hashes = recovery.hash_answers({"Q01": "새 학교", "Q02": "새별명", "Q03": "새동네"})
    s_reset = Session(engine)
    pre_member = s_reset.get(UserModel, 1)
    pre_rows = recovery_repo.answers_for(s_reset, 1)     # 미리 읽어 둔 옛 답 객체
    s_reset.commit()
    assert pre_member is not None and len(pre_rows) == 3
    s_put = Session(engine)
    locked = settings_repo.lock_active_member(s_put, 1)
    recovery_repo.replace_answers(s_put, locked.idx, new_hashes, "2026-11-09 10:00:00.000")
    result = {}

    def run_reset():
        body = FindPw(**identity(), answers=answers(), user_pw="Raced-1", user_pw_confirm="Raced-1")
        result["changed"] = accountServices.reset_password(s_reset, body, "10.0.0.1")

    t = threading.Thread(target=run_reset)
    t.start()
    time.sleep(1.5)
    assert t.is_alive()            # 회원 잠금에서 기다린다
    s_put.commit()
    t.join(20)
    s_put.close()
    s_reset.close()
    assert result["changed"] is False                 # 잠금 뒤 최신 조회로 옛 답 거절
    assert verify_password(PW, pw_of(engine))


def test_reset_commits_while_put_waits_put_rechecks_current_password(env):
    engine = env.engine
    s_put = Session(engine)
    s_put.get(UserModel, 1)                           # 미리 읽은 회원(옛 비밀번호 해시)
    s_put.commit()
    s_reset = Session(engine)
    user = account_repo.lock_member_for_reset(s_reset, **identity())
    account_repo.set_password(s_reset, user, hash_password("Reset-first"), "2026-11-09 10:00:00.000")
    before = answer_hash_rows(engine, 1)
    result = {}

    def run_put():
        body = RecoveryQuestionsPut(current_password=PW, answers=answers({"Q01": "x", "Q02": "y", "Q03": "z"}))
        try:
            recovery.save_answers(s_put, {"learner": {"idx": 1, "email_verified": True}}, body, "10.0.0.2")
            result["r"] = "saved"
        except Exception as exc:
            result["r"] = type(exc).__name__

    t = threading.Thread(target=run_put)
    t.start()
    time.sleep(1.5)
    assert t.is_alive()
    s_reset.commit()
    t.join(20)
    s_put.close()
    s_reset.close()
    assert result["r"] == "BadCurrentPassword"         # 최신 비밀번호 해시로 다시 확인 → 옛 비밀번호 거절
    assert answer_hash_rows(engine, 1) == before


# ── 유지보수 RB06 ───────────────────────────────────────────────────────────
MAINT_REQUESTS = [
    ("post", "/find-pw/verify", {"json": dict(identity(), answers=answers())}),
    ("post", "/find-pw/verify", {"content": b"{bad json", "headers": {"content-type": "application/json"}}),
    ("post", "/find-pw/verify", {"json": identity()}),
    ("post", "/find-pw", {"json": dict(identity(), answers=answers(), user_pw="M-1234", user_pw_confirm="M-1234")}),
    ("post", "/find-pw", {"json": dict(identity(), user_pw="M-1234", user_pw_confirm="M-1234")}),   # 질문 없는 옛 계약
    ("post", "/find-pw", {"content": b"", "headers": {}}),
    ("post", "/signup", {"json": {"user_id": "m@example.test", "user_pw": "pass-1234", "user_name": "m", "user_birth": "2000-01-01",
                                  "user_un": "u", "user_sp": "s", "recovery_answers": answers()}}),
    ("post", "/signup", {"content": b"{", "headers": {"content-type": "application/json"}}),
    ("post", "/signup", {"json": {"user_id": "m2@example.test", "user_pw": "pass-1234", "user_name": "m", "user_birth": "2000-01-01", "user_un": "u", "user_sp": "s"}}),
    ("put", "/me/recovery-questions", {"json": {"current_password": PW, "answers": answers()}, "headers": ORIGIN}),
    ("put", "/me/recovery-questions", {"content": b"x", "headers": ORIGIN}),
    ("get", "/me/recovery-questions", {}),
]


def test_maintenance_blocks_before_db_and_bcrypt(env, bc, monkeypatch):
    env.login(1)
    before = (counts(env.engine), pw_of(env.engine), answer_hash_rows(env.engine, 1))
    monkeypatch.setenv(safe.MAINTENANCE_ENV, "1")
    env.app_ref.state.db_opens = 0
    bc.reset()
    n_statements = len(env.statements)
    for method, path, kwargs in MAINT_REQUESTS:
        kwargs = dict(kwargs)
        res = getattr(env, method)(path, **kwargs) if method == "get" else env.request(method.upper(), path, **kwargs)
        assert res.status_code == 503, (method, path, res.status_code, res.text)
        assert res.json() == {"ok": False, "code": "maintenance", "detail": safe.MAINTENANCE}
        assert res.headers["cache-control"] == "no-store"
    assert bc.total == 0 and env.app_ref.state.db_opens == 0 and len(env.statements) == n_statements
    # 다른 기능(로그인 상태 조회·아이디 찾기·정보 수정 조회)은 막지 않는다.
    assert env.get("/me/settings").status_code == 200
    assert env.post("/find-id", json={"user_name": "에이", "user_birth": "1999-09-09"}).status_code == 200
    monkeypatch.delenv(safe.MAINTENANCE_ENV)
    assert (counts(env.engine), pw_of(env.engine), answer_hash_rows(env.engine, 1)) == before


@pytest.mark.parametrize("value", ["0", "", "true", "yes", " 2"])
def test_maintenance_only_exact_one(env, monkeypatch, value):
    monkeypatch.setenv(safe.MAINTENANCE_ENV, value)
    assert verify(env).status_code == 200


def test_flag_off_only_b_contract(env, monkeypatch):
    monkeypatch.delenv(safe.MAINTENANCE_ENV, raising=False)
    before = pw_of(env.engine)
    assert env.post("/find-pw", json=dict(identity(), user_pw="M-1234", user_pw_confirm="M-1234")).status_code == 422
    assert pw_of(env.engine) == before


def test_no_hidden_bypass_header_or_query(env, monkeypatch):
    monkeypatch.setenv(safe.MAINTENANCE_ENV, "1")
    for headers in ({"X-Maintenance-Bypass": "1"}, {"X-Admin": "1"}, {"Authorization": "Bearer x"}):
        assert env.post("/find-pw/verify?bypass=1", json=dict(identity(), answers=answers()), headers=headers).status_code == 503


def test_table_still_present_and_no_drop_in_code():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("services/accountRecoveryServices.py", "repositories/accountRecoveryRepositories.py", "routers/accountSafeErrors.py",
                "routers/learnerSignup.py", "routers/accountSettings.py", "routers/account.py", "docker/postgres/user_recovery_answer.sql"):
        assert "drop table" not in (root / rel).read_text(encoding="utf-8").lower()


# ── 스키마 누락(테이블 없음): 고정 500·쓰기 0, 404 와 구분 ─────────────────
def test_missing_table_is_fixed_500_not_404(env, tmp_path):
    engine = env.engine
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE user_recovery_answer RENAME TO user_recovery_answer_hidden"))
    try:
        before = pw_of(engine)
        with capture_logs(tmp_path) as logs:
            r1 = verify(env)
            r2 = reset(env)
            env.login(1)
            r3 = put(env)
            r4 = env.post("/signup", json={"user_id": "t@example.test", "user_pw": "pass-1234", "user_name": "t", "user_birth": "2000-01-01",
                                           "user_un": "u", "user_sp": "s", "recovery_answers": answers()})
        for res in (r1, r2, r3, r4):
            assert res.status_code == 500 and res.json() == {"ok": False, "detail": "서버 처리 중 문제가 생겼습니다."}
        assert any("category=db_programming" in line for line in logs["memory"])
        assert pw_of(engine) == before
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM user_member WHERE user_id='t@example.test'")).scalar_one() == 0
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE user_recovery_answer_hidden RENAME TO user_recovery_answer"))
