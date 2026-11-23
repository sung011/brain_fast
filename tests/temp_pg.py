"""임시 PostgreSQL 준비.

pgserver(내장)를 우선하고, Python 버전에 없으면 testing.postgresql + 로컬 initdb 로 대체한다.
프로젝트 DB·.env 와 무관하며, cleanup 으로 지운다.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

HAVE_DB_TOOLS = False
_BACKEND: str | None = None

try:
    import pgserver  # noqa: F401

    HAVE_DB_TOOLS = True
    _BACKEND = "pgserver"
except ImportError:
    try:
        import testing.postgresql  # noqa: F401

        HAVE_DB_TOOLS = True
        _BACKEND = "testing.postgresql"
    except ImportError:
        pass


class TempPg:
    """pgserver / testing.postgresql 공통 핸들. get_uri·cleanup 만 맞춘다."""

    def __init__(self, server, tmp: Path | None, backend: str):
        self._server = server
        self.tmp = tmp
        self.backend = backend

    def get_uri(self) -> str:
        if self.backend == "pgserver":
            return self._server.get_uri()
        return self._server.url()

    def cleanup(self) -> None:
        if self.backend == "pgserver":
            self._server.cleanup()
        else:
            self._server.stop()
        if self.tmp is not None:
            shutil.rmtree(self.tmp, ignore_errors=True)


def start_temp_pg(prefix: str = "brain_fast_pg_") -> TempPg:
    if not HAVE_DB_TOOLS:
        raise RuntimeError(
            "임시 DB 시험에는 pgserver 또는 testing.postgresql 이 필요합니다. "
            'pip install "testing.postgresql" 또는 pgserver 를 설치하세요.'
        )
    if _BACKEND == "pgserver":
        import pgserver

        tmp = Path(tempfile.mkdtemp(prefix=prefix))
        server = pgserver.get_server(tmp / "pg", cleanup_mode="stop")
        return TempPg(server, tmp, "pgserver")

    import testing.postgresql

    return TempPg(testing.postgresql.Postgresql(), None, "testing.postgresql")


def sqlalchemy_url(uri: str) -> str:
    if uri.startswith("postgresql+psycopg://"):
        return uri
    if uri.startswith("postgresql://"):
        return "postgresql+psycopg://" + uri[len("postgresql://") :]
    return uri
