"""가입 이메일 DDL 전후에 백엔드 DB 역할로 읽기 전용 대조를 실행한다."""
import json
import os
import re
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TABLE = "public.signup_email_code"
COLUMNS = [
    ("email_key", "character varying(64)"),
    ("code_digest", "character varying(64)"),
    ("sent_at", "timestamp with time zone"),
    ("expires_at", "timestamp with time zone"),
    ("failed_attempts", "integer"),
]
CONSTRAINTS = {
    ("p", "primarykeyemail_key"),
    ("c", "checkfailed_attempts>=0andfailed_attempts<=5"),
    ("c", "checksent_at<expires_at"),
}
INDEXES = {
    "createuniqueindexsignup_email_code_pkeyonsignup_email_codeusingbtreeemail_key",
    "createindexsignup_email_code_expires_idxonsignup_email_codeusingbtreeexpires_at",
}


def _rows(conn, sql, **params):
    return conn.execute(text(sql), params).mappings().all()


def _scalar(conn, sql, **params):
    return conn.execute(text(sql), params).scalar_one()


def _normalize(sql):
    return re.sub(r"[\s()]", "", sql.lower().replace("public.", ""))


def _table(conn, name):
    rows = _rows(conn, """
        SELECT oid, relkind, relowner, pg_get_userbyid(relowner) = current_user AS owned,
               relrowsecurity, relforcerowsecurity
        FROM pg_class WHERE oid = to_regclass(:name)
    """, name=name)
    return rows[0] if rows else None


def _member_column(conn, member):
    return _rows(conn, """
        SELECT a.attnum, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull AS required,
               EXISTS (
                   SELECT FROM pg_constraint c
                   JOIN pg_index i ON i.indexrelid = c.conindid
                   WHERE c.conrelid = a.attrelid AND c.contype = 'u'
                     AND c.conkey = ARRAY[a.attnum] AND c.convalidated AND NOT c.condeferrable
                     AND i.indisvalid AND c.conname = 'user_member_user_id_key'
               ) AS expected_unique
        FROM pg_attribute a
        WHERE a.attrelid = :oid AND a.attname = 'user_id' AND NOT a.attisdropped
    """, oid=member["oid"])


def _dependencies(conn, member, column):
    return _rows(conn, """
        SELECT d.classid::regclass::text AS catalog, d.deptype AS dependency_type,
               obj.type AS object_type, obj.identity AS identity, d.refobjsubid <> 0 AS column_ref
        FROM pg_depend d
        CROSS JOIN LATERAL pg_identify_object(d.classid, d.objid, d.objsubid) obj
        WHERE d.refclassid = 'pg_class'::regclass AND d.refobjid = :oid
          AND d.refobjsubid IN (0, :attnum)
        ORDER BY catalog, identity, dependency_type
    """, oid=member["oid"], attnum=column["attnum"])


def _inspect_shape(conn, oid, problems):
    columns = _rows(conn, """
        SELECT attname, format_type(atttypid, atttypmod) AS type,
               attnotnull, atthasdef, attidentity, attgenerated
        FROM pg_attribute WHERE attrelid = :oid AND attnum > 0 AND NOT attisdropped
        ORDER BY attnum
    """, oid=oid)
    if ([(c["attname"], c["type"]) for c in columns] != COLUMNS
            or any(not c["attnotnull"] or c["atthasdef"] or c["attidentity"] or c["attgenerated"] for c in columns)):
        problems.append("code_columns")

    constraints = _rows(conn, """
        SELECT contype, pg_get_constraintdef(oid) AS definition, convalidated, condeferrable
        FROM pg_constraint WHERE conrelid = :oid AND contype <> 'n'
    """, oid=oid)
    actual = {(c["contype"], _normalize(c["definition"])) for c in constraints if c["convalidated"] and not c["condeferrable"]}
    if len(constraints) != len(CONSTRAINTS) or actual != CONSTRAINTS:
        problems.append("code_constraints")

    indexes = _rows(conn, """
        SELECT pg_get_indexdef(indexrelid) AS definition, indisvalid, indisready
        FROM pg_index WHERE indrelid = :oid
    """, oid=oid)
    if len(indexes) != len(INDEXES) or {_normalize(i["definition"]) for i in indexes if i["indisvalid"] and i["indisready"]} != INDEXES:
        problems.append("code_indexes")
    if _scalar(conn, "SELECT count(*) FROM pg_trigger WHERE tgrelid = :oid AND NOT tgisinternal", oid=oid):
        problems.append("code_triggers")


def _inspect_security(conn, table, problems):
    oid = table["oid"]
    if not table["owned"]:
        problems.append("code_owner")
    if not table["relrowsecurity"] or table["relforcerowsecurity"]:
        problems.append("code_rls")
    if _scalar(conn, "SELECT count(*) FROM pg_policy WHERE polrelid = :oid", oid=oid):
        problems.append("code_policies")
    if _scalar(conn, """
        SELECT EXISTS (
            SELECT FROM pg_class c, aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) acl
            WHERE c.oid = :oid AND acl.grantee = 0
            UNION ALL
            SELECT FROM pg_attribute a, aclexplode(a.attacl) acl
            WHERE a.attrelid = :oid AND a.attnum > 0 AND NOT a.attisdropped AND acl.grantee = 0
        )
    """, oid=oid):
        problems.append("code_public_acl")

    roles = _rows(conn, """
        SELECT oid, rolname, rolsuper, rolbypassrls,
               pg_has_role(oid, :owner, 'MEMBER') AS owner_member
        FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')
    """, owner=table["relowner"])
    if not {"anon", "authenticated"}.issubset(role["rolname"] for role in roles):
        problems.append("code_api_roles_missing")

    privileges = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]
    if _scalar(conn, "SELECT current_setting('server_version_num')::integer") >= 170000:
        privileges.append("MAINTAIN")
    for role in roles:
        if role["rolsuper"] or role["owner_member"] or (role["rolbypassrls"] and role["rolname"] != "service_role"):
            problems.append(f"code_role_{role['rolname']}")
        if _scalar(conn, """
            SELECT EXISTS (
                SELECT FROM unnest(cast(:privileges AS text[])) AS p(name)
                WHERE has_table_privilege(:role, :oid, p.name)
            ) OR has_any_column_privilege(:role, :oid, 'SELECT, INSERT, UPDATE, REFERENCES')
        """, privileges=privileges, role=role["oid"], oid=oid):
            problems.append(f"code_grants_{role['rolname']}")


def inspect(conn, phase):
    """회원·코드 행을 읽지 않고 현재 트랜잭션에서 시스템 카탈로그를 검사한다."""
    if phase not in {"pre", "post"}:
        raise ValueError("invalid phase")
    conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    conn.exec_driver_sql("SET LOCAL search_path = pg_catalog")
    problems = []
    report = {"phase": phase, "user_id_type": None, "dependencies": [], "problems": problems}
    member = _table(conn, "public.user_member")
    if member is None or member["relkind"] != "r" or not member["owned"]:
        problems.append("member_table_or_owner")
    if phase == "pre" and not _scalar(conn, "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"):
        problems.append("public_create")
    if member is not None:
        columns = _member_column(conn, member)
        if not columns:
            problems.append("member_id_missing")
        else:
            column = columns[0]
            report["user_id_type"] = column["type"]
            expected_type = "character varying(30)" if phase == "pre" else "character varying(254)"
            if column["type"] != expected_type or not column["required"] or not column["expected_unique"]:
                problems.append("member_id_contract")
            report["dependencies"] = [dict(row) for row in _dependencies(conn, member, column)]
            if phase == "pre" and any(
                row["column_ref"] and row["catalog"] in {"pg_rewrite", "pg_policy"} for row in report["dependencies"]
            ):
                problems.append("member_id_view_or_policy")

    table = _table(conn, TABLE)
    if phase == "pre":
        if table is not None:
            problems.append("code_table_already_exists")
    elif table is None or table["relkind"] != "r":
        problems.append("code_table_missing_or_wrong_kind")
    else:
        _inspect_shape(conn, table["oid"], problems)
        _inspect_security(conn, table, problems)
    report["status"] = "MISMATCH" if problems else ("ABSENT" if phase == "pre" else "MATCH")
    return report


def main(args):
    engine = None
    try:
        if args not in (["pre"], ["post"]):
            raise ValueError("use pre or post")
        url = make_url(os.environ["SIGNUP_SCHEMA_DB_URL"])
        if url.drivername != "postgresql+psycopg" or not url.host or not url.database:
            raise ValueError("invalid database configuration")
        engine = create_engine(url, echo=False, hide_parameters=True)
        with engine.connect() as conn:
            report = inspect(conn, args[0])
        print(json.dumps(report, ensure_ascii=False))
        return 2 if report["problems"] else 0
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "type": type(exc).__name__}))
        return 3
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

