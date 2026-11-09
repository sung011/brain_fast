"""복구 B안 시험 공용 준비물(임시 내장 PostgreSQL·합성 회원만). conftest 가 아니라 필요한 시험이 직접 import 한다.

- 임시 PG 하나를 만들고 회원·문제·제출 테이블은 모델로, user_recovery_answer 는 **실제 DDL 파일**(docker/postgres/user_recovery_answer.sql)로 만든다.
- 앱에는 공용 exception_handlers 도 붙인다. 답 주소의 오류가 공용 처리기로 새어 나가면 입력값이 응답·로그에 실리므로 그 경우도 잡힌다.
- bcrypt 호출 수(hashpw·checkpw)와 SQL 실행 수를 센다. 프로젝트 DB·실제 회원·.env 와 무관하며 끝나면 지운다.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import bcrypt
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from db import Base, get_db
from exception_handlers import http_exception_handler, unhandled_exception_handler, validation_exception_handler
from logging_setup import ERROR_LOGGER
from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from models.userModel import UserModel
from models.userRecoveryAnswerModel import UserRecoveryAnswerModel
from routers.account import router as account_router
from routers.user import router as user_router
from services import accountServices, accountSettingsServices
from utils.password import hash_password

ROOT = Path(__file__).resolve().parents[1]
DDL_PATH = ROOT / "docker" / "postgres" / "user_recovery_answer.sql"
ORIGIN = {"Origin": "http://testserver"}
PW = "Pass-1234"
ANS = {"Q01": "합성 초등학교", "Q02": "합성별명", "Q03": "합성동"}


def answers(values: dict[str, str] | None = None) -> list[dict]:
    values = values or ANS
    return [{"question_id": q, "answer": values[q]} for q in ("Q01", "Q02", "Q03")]


def identity(user_id="a@example.test") -> dict:
    return {"user_id": user_id, "user_name": "에이", "user_birth": "1999-09-09"}


class BcryptCounter:
    def __init__(self):
        self.hash = 0
        self.check = 0
        self._orig_hash = bcrypt.hashpw
        self._orig_check = bcrypt.checkpw

    def install(self, monkeypatch):
        def counted_hash(pw, salt):
            self.hash += 1
            return self._orig_hash(pw, salt)

        def counted_check(pw, hashed):
            self.check += 1
            return self._orig_check(pw, hashed)

        monkeypatch.setattr(bcrypt, "hashpw", counted_hash)
        monkeypatch.setattr(bcrypt, "checkpw", counted_check)
        return self

    def reset(self):
        self.hash = 0
        self.check = 0

    @property
    def total(self):
        return self.hash + self.check


def start_pg():
    import pgserver

    tmp = Path(tempfile.mkdtemp(prefix="recovery_b_pg_"))
    server = pgserver.get_server(tmp / "pg", cleanup_mode="stop")
    engine = create_engine(server.get_uri().replace("postgresql://", "postgresql+psycopg://", 1), pool_size=10, max_overflow=5)
    Base.metadata.create_all(engine, tables=[UserModel.__table__, StudyModel.__table__, ReviewNodeModel.__table__])
    apply_ddl(engine)
    return tmp, server, engine


def apply_ddl(engine):
    sql = DDL_PATH.read_text(encoding="utf-8")
    raw = engine.raw_connection()
    try:
        raw.autocommit = True   # 파일 안의 BEGIN/COMMIT 이 트랜잭션을 정한다
        with raw.cursor() as cur:
            cur.execute(sql)
    finally:
        raw.close()


def stop_pg(tmp, server, engine):
    engine.dispose()
    server.cleanup()
    shutil.rmtree(tmp, ignore_errors=True)


def seed(engine, pw_hash, answer_hashes):
    """회원 1·2(답 등록), 3(탈퇴, 답 등록), 4(미등록), 5(미승인 표시용, 답 등록)."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE user_recovery_answer, review_node, study, user_member RESTART IDENTITY CASCADE"))
    with Session(engine) as db:
        def member(idx, uid, name="에이", del_yn="N"):
            return UserModel(idx=idx, user_id=uid, user_name=name, user_birth="1999-09-09", user_pw=pw_hash, user_un="가학교",
                             user_sp="나전공", del_yn=del_yn, state="N", mb_level=1, updated_at="2026-11-01 09:00:00.000")
        db.add_all([member(1, "a@example.test"), member(2, "b@example.test", name="비"), member(3, "gone@example.test", del_yn="Y"),
                    member(4, "noans@example.test"), member(5, "e@example.test")])
        db.flush()
        for user_idx in (1, 2, 3, 5):
            for qid, h in answer_hashes.items():
                db.add(UserRecoveryAnswerModel(ura_u_idx=user_idx, ura_question_id=qid, ura_answer_hash=h))
        db.commit()
    with engine.begin() as conn:   # 다음 가입이 idx 를 겹치지 않게
        conn.execute(text("SELECT setval(pg_get_serial_sequence('user_member','idx'), 100)"))


def counts(engine, user_idx=None):
    with engine.connect() as conn:
        members = conn.execute(text("SELECT count(*) FROM user_member")).scalar_one()
        if user_idx is None:
            rows = conn.execute(text("SELECT count(*) FROM user_recovery_answer")).scalar_one()
        else:
            rows = conn.execute(text("SELECT count(*) FROM user_recovery_answer WHERE ura_u_idx=:u"), {"u": user_idx}).scalar_one()
    return members, rows


def answer_hash_rows(engine, user_idx):
    with engine.connect() as conn:
        return dict(conn.execute(text("SELECT ura_question_id, ura_answer_hash FROM user_recovery_answer WHERE ura_u_idx=:u ORDER BY 1"),
                                 {"u": user_idx}).all())


def build_app(engine, session_cls=Session):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(user_router)
    app.include_router(account_router)

    @app.post("/_set")
    async def _set(request: Request):
        body = await request.json()
        request.session.clear()
        request.session.update(body)
        return {"ok": True}

    app.state.db_opens = 0

    def test_db():
        app.state.db_opens += 1
        with app.state.session_cls(engine) as db:
            yield db

    app.state.session_cls = session_cls
    app.dependency_overrides[get_db] = test_db
    return app


@contextmanager
def client_for(engine):
    accountServices.ip_limiter.reset()
    accountServices.identity_limiter.reset()
    accountSettingsServices.learner_limiter.reset()
    accountSettingsServices.ip_limiter.reset()
    app = build_app(engine)
    statements: list[str] = []
    listener = lambda conn, cur, stmt, params, ctx, many: statements.append(stmt)  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            client.statements = statements
            client.engine = engine
            client.app_ref = app
            client.login = lambda idx=1, verified=True: client.post("/_set", json={"learner": {"idx": idx, "email_verified": verified}})
            yield client
    finally:
        event.remove(engine, "before_cursor_execute", listener)


@contextmanager
def capture_logs(tmp_dir: Path):
    """메모리(caplog 대신 직접) + 파일 로그를 함께 잡는다. 공용 오류 로거(brain_fast.error)도 포함한다."""
    path = tmp_dir / "captured.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    memory: list[str] = []

    class Mem(logging.Handler):
        def emit(self, record):
            try:
                memory.append(record.getMessage())
            except Exception:
                memory.append(str(record.msg))

    mem = Mem(level=logging.DEBUG)
    targets = [logging.getLogger(), logging.getLogger(ERROR_LOGGER), logging.getLogger("account_api")]
    old_levels = [t.level for t in targets]
    for t in targets:
        t.addHandler(handler)
        t.addHandler(mem)
        t.setLevel(logging.DEBUG)
    box = {"memory": memory, "path": path}
    try:
        yield box
    finally:
        for t, lvl in zip(targets, old_levels):
            t.removeHandler(handler)
            t.removeHandler(mem)
            t.setLevel(lvl)
        handler.close()
        box["file"] = path.read_text(encoding="utf-8") if path.exists() else ""


def make_hashes():
    return {q: hash_password(v) for q, v in ANS.items()}
