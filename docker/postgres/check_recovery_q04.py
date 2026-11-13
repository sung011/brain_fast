"""Q04 질문 추가 DDL(user_recovery_answer_q04.sql) 전후에 백엔드 DB 역할로 읽기 전용 대조를 한다.

- pre: 표 소유자 = 현재 역할, 같은 이름 CHECK 의 **식 전체**가 정확히 IN ('Q01','Q02','Q03') 이고 검증 완료면 READY.
- post: 같은 이름 CHECK 의 식 전체가 정확히 IN ('Q01','Q02','Q03','Q04') 이고 검증 완료(convalidated)면 MATCH.
- 비교는 pg_get_constraintdef 를 정규화한 전체 문자열과 허용 형태(IN 목록·= ANY(ARRAY) 두 표기)의 정확 일치다.
  리터럴은 대소문자·공백까지 그대로 비교한다. OR·AND·NOT·다른 칼럼·다른 형 변환 등 알려지지 않은 형태는 모두 MISMATCH.
- 답 행은 읽지 않는다(시스템 카탈로그만). 주소는 RECOVERY_Q04_DB_URL 환경변수(postgresql+psycopg)로만 받는다.
- 종료 코드: 0 정상, 2 불일치, 3 오류(오류 종류 이름만 출력).
"""
import json
import os
import re
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

CONSTRAINT = "user_recovery_answer_qid_check"
BEFORE = ["Q01", "Q02", "Q03"]
AFTER = ["Q01", "Q02", "Q03", "Q04"]
# SQL 문자열 리터럴('...', 안의 '' 는 따옴표 하나). 정규화하지 않고 원문 그대로 둔다.
_LITERAL = re.compile(r"'(?:[^']|'')*'")
# PostgreSQL 이 이 제약을 출력할 때 붙이는 형 변환만 지운다(이름을 명시. 그 밖의 형 변환은 남겨 불일치로 닫는다).
_CAST = re.compile(r"::(?:character varying|text)(?:\[\])?")


def _norm_code(part):
    """리터럴 밖: 소문자, 알려진 형 변환·괄호·공백 제거."""
    return re.sub(r"[()\s]", "", _CAST.sub("", part.lower()))


def normalize(definition):
    """제약식 전체를 비교용 문자열로. 리터럴은 한 글자도 바꾸지 않는다."""
    out, pos = [], 0
    for m in _LITERAL.finditer(definition):
        out.append(_norm_code(definition[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_norm_code(definition[pos:]))
    return "".join(out)


def _forms(ids):
    listed = ",".join(f"'{q}'" for q in ids)
    return {f"checkura_question_id=anyarray[{listed}]", f"checkura_question_idin{listed}"}


ALLOWED = {"pre": _forms(BEFORE), "post": _forms(AFTER)}


def inspect(conn, phase):
    if phase not in {"pre", "post"}:
        raise ValueError("invalid phase")
    conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    conn.exec_driver_sql("SET LOCAL search_path = pg_catalog")
    problems = []
    report = {"phase": phase, "question_ids": None, "problems": problems}
    table = conn.execute(text(
        "SELECT c.oid, c.relkind, pg_get_userbyid(c.relowner) = current_user AS owned "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = 'user_recovery_answer'")).mappings().first()
    if table is None or table["relkind"] != "r" or not table["owned"]:
        problems.append("answer_table_or_owner")
    else:
        rows = conn.execute(text(
            "SELECT contype, convalidated, pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = :oid AND conname = :name"), {"oid": table["oid"], "name": CONSTRAINT}).mappings().all()
        if len(rows) != 1 or rows[0]["contype"] != "c":
            problems.append("qid_check_missing")
        else:
            normalized = normalize(rows[0]["def"])
            report["question_ids"] = [lit[1:-1] for lit in _LITERAL.findall(rows[0]["def"])]
            if normalized not in ALLOWED[phase]:
                problems.append("qid_check_expression")
            if not rows[0]["convalidated"]:
                problems.append("qid_check_not_validated")
    report["status"] = "MISMATCH" if problems else ("READY" if phase == "pre" else "MATCH")
    return report


def main(args):
    engine = None
    try:
        if args not in (["pre"], ["post"]):
            raise ValueError("use pre or post")
        url = make_url(os.environ["RECOVERY_Q04_DB_URL"])
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
