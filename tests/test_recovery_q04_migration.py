"""r4 회귀: Q04 허용 DDL(user_recovery_answer_q04.sql)과 읽기 전용 검사기(check_recovery_q04.py). 임시 PG·합성 역할만.

기존 공유 DB 처럼 Q01~Q03 제약인 표를 만든 뒤 pre READY → DDL → post MATCH 를 확인한다.
"""
import importlib.util
import os
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from recovery_b_support import PW, app_engine, apply_ddl, start_pg, stop_pg
from utils.password import hash_password

ROOT = Path(__file__).resolve().parents[1]
PG_DIR = ROOT / "docker" / "postgres"
_spec = importlib.util.spec_from_file_location("check_recovery_q04", PG_DIR / "check_recovery_q04.py")
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)
OLD_CHECK = ("ALTER TABLE user_recovery_answer DROP CONSTRAINT user_recovery_answer_qid_check, "
             "ADD CONSTRAINT user_recovery_answer_qid_check CHECK (ura_question_id IN ('Q01','Q02','Q03'))")


def _recovery_tool():
    """설정 폴더 복구 B 검사기(RECOVERY_TOOL 로 경로를 바꿀 수 있다). 없으면 그 확인만 건너뛴다."""
    path = Path(os.environ.get("RECOVERY_TOOL", r"C:\Users\user\Desktop\setting\scripts\recovery_b\check_recovery_schema.py"))
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("check_recovery_schema_r4", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def legacy_pg():
    tmp, server, engine = start_pg()
    owner = app_engine(engine)
    try:
        with owner.begin() as conn:
            conn.execute(text(OLD_CHECK))
            conn.execute(text("INSERT INTO user_member (user_id, user_pw, user_name, user_birth, user_un, user_sp, del_yn, state, mb_level) "
                              "VALUES ('legacy@example.test', :pw, '합성', '1999-09-09', '학교', '전공', 'N', 'N', 1)"), {"pw": hash_password(PW)})
            idx = conn.execute(text("SELECT idx FROM user_member")).scalar_one()
            for q in ("Q01", "Q02", "Q03"):
                conn.execute(text("INSERT INTO user_recovery_answer (ura_u_idx, ura_question_id, ura_answer_hash) VALUES (:i, :q, :h)"),
                             {"i": idx, "q": q, "h": "$2b$12$" + "a" * 53})
        yield engine, owner, idx
    finally:
        owner.dispose()
        stop_pg(tmp, server, engine)


def _check(eng, phase):
    seen = []
    listener = lambda conn, cur, stmt, params, ctx, many: seen.append(" ".join(stmt.split()))  # noqa: E731
    event.listen(eng, "before_cursor_execute", listener)
    try:
        with eng.connect() as conn:
            rep = checker.inspect(conn, phase)
    finally:
        event.remove(eng, "before_cursor_execute", listener)
    return rep, seen


def test_q04_migration_pre_ddl_post(legacy_pg):
    engine, owner, idx = legacy_pg
    pre, seen_pre = _check(owner, "pre")
    with pytest.raises(IntegrityError):
        with owner.begin() as conn:
            conn.execute(text("INSERT INTO user_recovery_answer (ura_u_idx, ura_question_id, ura_answer_hash) VALUES (:i, 'Q04', 'h')"), {"i": idx})
    apply_ddl(engine, (PG_DIR / "user_recovery_answer_q04.sql").read_text(encoding="utf-8"))
    post, seen_post = _check(owner, "post")
    pre_again, _ = _check(owner, "pre")
    with owner.connect() as conn:
        kept = conn.execute(text("SELECT ura_question_id FROM user_recovery_answer ORDER BY 1")).scalars().all()
    kinds = {s.split()[0].upper() for s in seen_pre + seen_post}
    reads = [s for s in seen_pre + seen_post if "from user_recovery_answer" in s.lower() or "from public.user_recovery_answer" in s.lower()]
    print(f"\nPROBE r4 q04_migration pre={pre['status']}:{pre['question_ids']} post={post['status']}:{post['question_ids']} pre_after={pre_again['status']} kept={kept} kinds={sorted(kinds)} row_reads={len(reads)}")
    assert pre["status"] == "READY" and pre["question_ids"] == ["Q01", "Q02", "Q03"]
    assert post["status"] == "MATCH" and post["question_ids"] == ["Q01", "Q02", "Q03", "Q04"]
    assert pre_again["status"] == "MISMATCH"
    assert kept == ["Q01", "Q02", "Q03"] and kinds <= {"SET", "SELECT"} and not reads
    with owner.begin() as conn:
        conn.execute(text("INSERT INTO user_recovery_answer (ura_u_idx, ura_question_id, ura_answer_hash) VALUES (:i, 'Q04', 'h')"), {"i": idx})
    tool = _recovery_tool()
    if tool is not None:
        rep = tool.check(owner)
        print(f"PROBE r4 recovery_b_checker result={rep['result']} variant={rep.get('qid_variant')} problems={rep['problems']}")
        assert rep["result"] == "MATCH" and rep.get("qid_variant") == "q04"


def test_q04_checker_non_owner_and_wrong_list(legacy_pg):
    engine, owner, _ = legacy_pg
    with engine.connect() as conn:   # 관리자 역할(소유자 아님)
        as_admin = checker.inspect(conn, "pre")
    with owner.begin() as conn:
        conn.execute(text("ALTER TABLE user_recovery_answer DROP CONSTRAINT user_recovery_answer_qid_check, "
                          "ADD CONSTRAINT user_recovery_answer_qid_check CHECK (ura_question_id IN ('Q01','Q02','Q03','Q04','Q05'))"))
    wide, _ = _check(owner, "post")
    print(f"\nPROBE r4 q04_checker non_owner={as_admin['status']} wide={wide['status']}:{wide['question_ids']}")
    assert as_admin["status"] == "MISMATCH" and "answer_table_or_owner" in as_admin["problems"]
    assert wide["status"] == "MISMATCH" and "qid_check_expression" in wide["problems"]


def test_q04_insert_before_migration_is_fixed_500(legacy_pg, monkeypatch):
    """DDL 전에 새 코드로 Q04 를 저장하면 DB 가 거절 → 고정 500(내부 문구 노출 없음). 적용 순서(DDL 먼저)의 근거."""
    from recovery_b_support import client_for
    engine, owner, idx = legacy_pg
    with client_for(owner) as client:
        client.login(idx)
        res = client.put("/me/recovery-questions", json={"current_password": PW, "answers": [{"question_id": "Q04", "answer": "a"}]},
                         headers={"Origin": "http://testserver"})
    print(f"\nPROBE r4 q04_before_ddl status={res.status_code}")
    with owner.connect() as conn:
        kept = conn.execute(text("SELECT ura_question_id FROM user_recovery_answer ORDER BY 1")).scalars().all()
    assert res.status_code == 500 and "check" not in res.text.lower() and "Q04" not in res.text
    assert kept == ["Q01", "Q02", "Q03"]   # 실패하면 기존 세 답은 그대로(롤백)


# ── R4-001 회귀: 제약식 전체 비교. 값 목록이 같아도 다른 술어·형태면 READY/MATCH 를 내지 않는다 ──────────────
V1 = "'Q01','Q02','Q03'"
Q4 = "'Q01','Q02','Q03','Q04'"
BAD_EXPRESSIONS = {
    "or_true": "ura_question_id IN ({L}) OR true",
    "or_length": "ura_question_id IN ({L}) OR length(ura_question_id) > 0",
    "not_in": "ura_question_id NOT IN ({L})",
    "extra_and": "ura_question_id IN ({L}) AND length(ura_question_id) = 3",
    "other_column": "ura_question_set IN ({L})",
    "lower_literal": "lower(ura_question_id) IN ({L})",
    "case_variant": "ura_question_id IN ({LC})",
    "space_variant": "ura_question_id IN ({LS})",
    "or_null": "ura_question_id IN ({L}) OR ura_question_id IS NULL",
}
GOOD_EXPRESSIONS = {
    "in_list": "ura_question_id IN ({L})",
    "any_array": "ura_question_id = ANY (ARRAY[{L}])",
    "any_array_cast": "(ura_question_id)::text = ANY ((ARRAY[{L}])::text[])",
}


@pytest.fixture(scope="module")
def gate_pg():
    tmp, server, engine = start_pg()
    owner = app_engine(engine)
    with owner.begin() as conn:
        conn.execute(text("INSERT INTO user_member (user_id, user_pw, user_name, user_birth, user_un, user_sp, del_yn, state, mb_level) "
                          "VALUES ('gate@example.test', 'x', '합성', '1999-09-09', '학교', '전공', 'N', 'N', 1)"))
    try:
        yield engine, owner
    finally:
        owner.dispose()
        stop_pg(tmp, server, engine)


def _set_check(owner, expression, not_valid=False):
    with owner.begin() as conn:
        conn.execute(text("DELETE FROM user_recovery_answer"))
        conn.execute(text("ALTER TABLE user_recovery_answer DROP CONSTRAINT IF EXISTS user_recovery_answer_qid_check"))
        conn.execute(text(f"ALTER TABLE user_recovery_answer ADD CONSTRAINT user_recovery_answer_qid_check CHECK ({expression})"
                          + (" NOT VALID" if not_valid else "")))


def _fill(template, listed):
    return template.format(L=listed, LC=listed.replace("'Q01'", "'q01'"), LS=listed.replace("'Q01'", "'Q01 '"))


@pytest.mark.parametrize("phase", ["pre", "post"])
@pytest.mark.parametrize("name", list(BAD_EXPRESSIONS))
def test_gate_rejects_wrong_expression(gate_pg, phase, name):
    _, owner = gate_pg
    _set_check(owner, _fill(BAD_EXPRESSIONS[name], V1 if phase == "pre" else Q4))
    rep, seen = _check(owner, phase)
    tool = _recovery_tool()
    general = tool.check(owner)["result"] if tool else "SKIPPED"
    kinds = {x.split()[0].upper() for x in seen}
    print(f"\nPROBE r4 gate_bad {phase} {name} gate={rep['status']}:{rep['problems']} general={general}")
    assert rep["status"] == "MISMATCH" and "qid_check_expression" in rep["problems"]
    assert general in ("MISMATCH", "SKIPPED")      # 일반 복구 검사기와 같은 판단
    assert kinds <= {"SET", "SELECT"}


@pytest.mark.parametrize("phase", ["pre", "post"])
@pytest.mark.parametrize("name", list(GOOD_EXPRESSIONS))
def test_gate_accepts_exact_forms(gate_pg, phase, name):
    _, owner = gate_pg
    _set_check(owner, _fill(GOOD_EXPRESSIONS[name], V1 if phase == "pre" else Q4))
    rep, _ = _check(owner, phase)
    other, _ = _check(owner, "post" if phase == "pre" else "pre")
    tool = _recovery_tool()
    general = tool.check(owner) if tool else None
    print(f"\nPROBE r4 gate_good {phase} {name} gate={rep['status']} other_phase={other['status']} general={general and (general['result'], general.get('qid_variant'))}")
    assert rep["status"] == ("READY" if phase == "pre" else "MATCH") and not rep["problems"]
    assert other["status"] == "MISMATCH"      # pre 는 정확 v1 만, post 는 정확 q04 만
    if general is not None:
        assert general["result"] == "MATCH" and general["qid_variant"] == ("v1" if phase == "pre" else "q04")


@pytest.mark.parametrize("phase", ["pre", "post"])
def test_gate_rejects_not_valid(gate_pg, phase):
    _, owner = gate_pg
    _set_check(owner, _fill(GOOD_EXPRESSIONS["in_list"], V1 if phase == "pre" else Q4), not_valid=True)
    rep, _ = _check(owner, phase)
    tool = _recovery_tool()
    general = tool.check(owner)["result"] if tool else "SKIPPED"
    print(f"\nPROBE r4 gate_not_valid {phase} gate={rep['status']}:{rep['problems']} general={general}")
    # pg_get_constraintdef 가 ' NOT VALID' 를 붙이므로 식 불일치와 미검증 두 가지로 닫힌다(일반 검사기도 MISMATCH)
    assert rep["status"] == "MISMATCH" and "qid_check_not_validated" in rep["problems"]
    assert general in ("MISMATCH", "SKIPPED")


def test_gate_restores_exact_v1(gate_pg):
    """마지막에 정상 v1 으로 돌려 module 정리 전 상태를 고정한다."""
    _, owner = gate_pg
    _set_check(owner, _fill(GOOD_EXPRESSIONS["in_list"], V1))
    rep, _ = _check(owner, "pre")
    assert rep["status"] == "READY"
