"""이메일 스키마 전후 검사와 위험 변형을 합성 PostgreSQL에서 확인한다."""
import importlib.util

import pytest
from sqlalchemy import text

from recovery_b_support import ROOT, app_engine, apply_ddl, start_pg, stop_pg

path = ROOT / "docker/postgres/check_signup_email_schema.py"
spec = importlib.util.spec_from_file_location("check_signup_email_schema", path)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.fixture(scope="module")
def pg():
    tmp, server, engine = start_pg()
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE user_member ALTER COLUMN user_id TYPE varchar(30)"))
            conn.execute(text("CREATE ROLE service_role NOLOGIN BYPASSRLS"))
            conn.execute(text("ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public GRANT ALL ON TABLES TO service_role"))
            conn.execute(text("CREATE VIEW public.email_schema_probe AS SELECT user_id FROM public.user_member"))
        owner = app_engine(engine)
        try:
            with owner.connect() as conn:
                refused = gate.inspect(conn, "pre")
                assert conn.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
            assert refused["status"] == "MISMATCH" and "member_id_view_or_policy" in refused["problems"]
            assert any(row["catalog"] == "pg_rewrite" for row in refused["dependencies"])
            with engine.begin() as conn:
                conn.execute(text("DROP VIEW public.email_schema_probe"))
                conn.execute(text("CREATE VIEW public.email_schema_probe_other AS SELECT idx FROM public.user_member"))
            with owner.connect() as conn:
                assert gate.inspect(conn, "pre")["status"] == "ABSENT"
                assert gate.inspect(conn, "post")["status"] == "MISMATCH"
            apply_ddl(engine, (ROOT / "docker/postgres/signup_email_code.sql").read_text(encoding="utf-8"))
            yield engine, owner
        finally:
            owner.dispose()
    finally:
        stop_pg(tmp, server, engine)


def test_matches_installed_schema_and_refuses_reapply(pg):
    _, owner = pg
    with owner.connect() as conn:
        report = gate.inspect(conn, "post")
        assert report["status"] == "MATCH", report
    with owner.connect() as conn:
        assert "code_table_already_exists" in gate.inspect(conn, "pre")["problems"]


@pytest.mark.parametrize(("ddl", "problem"), [
    ("ALTER TABLE signup_email_code DISABLE ROW LEVEL SECURITY", "code_rls"),
    ("ALTER TABLE signup_email_code FORCE ROW LEVEL SECURITY", "code_rls"),
    ("CREATE POLICY probe ON signup_email_code FOR SELECT USING (true)", "code_policies"),
    ("GRANT TRUNCATE ON signup_email_code TO anon", "code_grants_anon"),
    ("GRANT REFERENCES (email_key) ON signup_email_code TO authenticated", "code_grants_authenticated"),
    ("GRANT TRIGGER ON signup_email_code TO authenticated", "code_grants_authenticated"),
    ("GRANT SELECT ON signup_email_code TO service_role", "code_grants_service_role"),
    ("GRANT SELECT (code_digest) ON signup_email_code TO PUBLIC", "code_public_acl"),
    ("ALTER TABLE signup_email_code OWNER TO CURRENT_USER", "code_owner"),
    ("ALTER TABLE signup_email_code ADD COLUMN probe text", "code_columns"),
    ("ALTER TABLE signup_email_code ADD CONSTRAINT probe_check CHECK (failed_attempts >= 0)", "code_constraints"),
    ("DROP INDEX signup_email_code_expires_idx", "code_indexes"),
])
def test_refuses_schema_drift(pg, ddl, problem):
    engine, owner = pg
    # 16/17 임시 DB의 변경은 항상 원복한다.
    cleanup = {
        "code_rls": ("ALTER TABLE signup_email_code ENABLE ROW LEVEL SECURITY",
                     "ALTER TABLE signup_email_code NO FORCE ROW LEVEL SECURITY"),
        "code_policies": ("DROP POLICY probe ON signup_email_code",),
        "code_grants_anon": ("REVOKE TRUNCATE ON signup_email_code FROM anon",),
        "code_grants_authenticated": ("REVOKE REFERENCES (email_key) ON signup_email_code FROM authenticated",
                                     "REVOKE TRIGGER ON signup_email_code FROM authenticated"),
        "code_grants_service_role": ("REVOKE SELECT ON signup_email_code FROM service_role",),
        "code_public_acl": ("REVOKE SELECT (code_digest) ON signup_email_code FROM PUBLIC",),
        "code_owner": ("ALTER TABLE signup_email_code OWNER TO app_owner",),
        "code_columns": ("ALTER TABLE signup_email_code DROP COLUMN probe",),
        "code_constraints": ("ALTER TABLE signup_email_code DROP CONSTRAINT probe_check",),
        "code_indexes": ("CREATE INDEX signup_email_code_expires_idx ON signup_email_code (expires_at)",),
    }[problem]
    with engine.begin() as conn:
        conn.execute(text(ddl))
    try:
        with owner.connect() as conn:
            report = gate.inspect(conn, "post")
            assert report["status"] == "MISMATCH" and problem in report["problems"], report
    finally:
        with engine.begin() as conn:
            for stmt in cleanup:
                conn.execute(text(stmt))


def test_rejects_invalid_config_without_showing_value(monkeypatch, capsys):
    monkeypatch.setenv("SIGNUP_SCHEMA_DB_URL", "synthetic-invalid-url")
    assert gate.main(["pre"]) == 3
    out = capsys.readouterr()
    assert '"status": "ERROR"' in out.out and "synthetic-invalid-url" not in out.out and not out.err

