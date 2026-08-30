"""
PostgreSQL 연결.

SQLAlchemy 엔진과 세션을 만든다.
라우터에서는 get_db() 를 Depends 로 받아서 쓰면 된다.
"""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """ORM 모델의 공통 부모. 테이블 모델은 이 클래스를 상속한다."""


def _in_docker() -> bool:
    return Path("/.dockerenv").exists()


def _host_reachable(host: str, port: int) -> bool:
    try:
        socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def _candidate_urls(raw: str) -> list[URL]:
    """
    Docker 안에서는 127.0.0.1 이 앱 컨테이너 자신이라 DB에 닿지 않는다.
    맥에서 돌아가는 PostgreSQL(mediscan_note)은 host.docker.internal 로 붙는다.
    """
    base = make_url(raw)
    databases = []
    for name in ("mediscan_note", base.database):
        if name and name not in databases:
            databases.append(name)

    hosts: list[tuple[str, int]] = []
    if _in_docker():
        # compose 네트워크 / Docker Desktop 단독 Run 모두 커버한다.
        hosts.extend(
            [
                ("db", 5432),
                ("brain_fast_db", 5432),
                ("host.docker.internal", 5433),
                ("host.docker.internal", 5432),
            ]
        )
    else:
        hosts.append(("127.0.0.1", 5432))
        if base.host and base.host not in {"127.0.0.1", "localhost"}:
            hosts.insert(0, (base.host, base.port or 5432))

    candidates: list[URL] = []
    for host, port in hosts:
        if host in {"db", "brain_fast_db"} and not _host_reachable(host, port):
            logger.info("DB 호스트 생략(이름 없음): %s:%s", host, port)
            continue
        for database in databases:
            candidates.append(base.set(host=host, port=port, database=database))

    unique: list[URL] = []
    seen: set[tuple[str | None, int | None, str | None]] = set()
    for url in candidates:
        key = (url.host, url.port, url.database)
        if key not in seen:
            seen.add(key)
            unique.append(url)
    return unique


def _target(url: URL) -> str:
    return f"{url.host}:{url.port or 5432}/{url.database}"


def _connect_args(url: URL) -> dict[str, object]:
    """IPv6로 빠지지 않게 IPv4 주소만 쓴다."""
    args: dict[str, object] = {"connect_timeout": 3}
    host = url.host
    if not host:
        return args
    try:
        infos = socket.getaddrinfo(
            host,
            url.port or 5432,
            socket.AF_INET,
            socket.SOCK_STREAM,
        )
        if infos:
            args["hostaddr"] = infos[0][4][0]
    except OSError:
        pass
    return args


def _make_engine(url: URL) -> Engine:
    return create_engine(url, pool_pre_ping=True, connect_args=_connect_args(url))


_initial = _candidate_urls(settings.database_url)[0]
engine = _make_engine(_initial)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """요청마다 DB 세션을 열고, 끝나면 닫는다."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping_db() -> str:
    """
    연결이 살아 있는지 확인하고, 연결된 데이터베이스 이름을 반환한다.
    연결에 실패하면 예외가 난다.
    """
    with engine.connect() as conn:
        name = conn.execute(text("SELECT current_database()")).scalar_one()
    return str(name)


def wait_for_db(retries: int = 20, delay_seconds: float = 1.0) -> str:
    """
    DB가 아직 안 떠 있으면 잠시 기다린다.
    Docker에서는 맥 PostgreSQL(host.docker.internal)을 먼저 시도한다.
    """
    candidates = _candidate_urls(settings.database_url)
    last_error: Exception | None = None
    tried = ", ".join(_target(url) for url in candidates) or "(없음)"
    logger.info("DB 연결 후보: %s", tried)
    if not candidates:
        raise RuntimeError("DB 연결 후보가 없습니다. host.docker.internal / db 를 확인하세요.")

    global engine, SessionLocal

    for attempt in range(1, retries + 1):
        for url in candidates:
            test_engine: Engine | None = None
            try:
                test_engine = _make_engine(url)
                with test_engine.connect() as conn:
                    name = conn.execute(text("SELECT current_database()")).scalar_one()
                engine = test_engine
                SessionLocal = sessionmaker(
                    bind=engine,
                    autoflush=False,
                    autocommit=False,
                    expire_on_commit=False,
                )
                logger.info("PostgreSQL 연결됨: %s (%s)", name, _target(url))
                return str(name)
            except Exception as exc:
                last_error = exc
                if test_engine is not None:
                    test_engine.dispose()
        logger.warning(
            "DB 연결 대기 중 (%s/%s) 후보=%s: %s",
            attempt,
            retries,
            tried,
            last_error,
        )
        time.sleep(delay_seconds)

    raise RuntimeError(f"PostgreSQL에 연결하지 못했습니다: {tried}") from last_error
