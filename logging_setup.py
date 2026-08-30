"""
오류 로그 설정 모듈.

역할:
- 오류가 나면 logs/YYYY-MM-DD/error.log 에 내용을 남긴다.
- 날짜가 바뀌면 새 날짜 폴더를 만들어 그쪽으로 기록을 이어간다.
- 7일이 지난 날짜 폴더는 안의 로그 파일까지 모두 삭제한다.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# 한국 시간(KST, UTC+9). 로그 폴더 날짜와 시각을 한국 기준으로 맞춘다.
KST = timezone(timedelta(hours=9))

# 로그 루트 폴더. 이 파일(logging_setup.py)이 있는 프로젝트 루트 아래 logs/
LOG_DIR = Path(__file__).resolve().parent / "logs"

# 로그를 보관할 기간(일). 오늘 포함 최근 7일만 남기고 그 이전은 삭제한다.
RETENTION_DAYS = 7

# 오류 전용 로거 이름. exception_handlers.py 에서도 같은 이름으로 가져다 쓴다.
ERROR_LOGGER = "brain_fast.error"

# 날짜 폴더 이름 형식(예: 2026-08-25). 이 형식만 오래된 로그로 보고 삭제한다.
_DATE_FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 한 줄 로그 형식: 시각 | 레벨 | 메시지
_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def today_kst() -> date:
    """지금 한국 날짜를 반환한다. 폴더 이름(YYYY-MM-DD)을 정할 때 사용한다."""
    return datetime.now(KST).date()


def purge_old_log_folders(
    log_dir: Path = LOG_DIR,
    retention_days: int = RETENTION_DAYS,
    today: date | None = None,
) -> list[Path]:
    """
    보관 기간이 지난 날짜 폴더를 통째로 지운다.

    예) 오늘이 2026-08-25 이고 retention_days=7 이면
        2026-08-18 이전 폴더(error.log 포함)를 모두 삭제한다.
    반환값: 실제로 지운 폴더 경로 목록.
    """
    # 이 날짜 이하(오늘 - 7일)는 삭제 대상이다.
    cutoff = (today or today_kst()) - timedelta(days=retention_days)
    deleted: list[Path] = []
    if not log_dir.exists():
        return deleted

    for path in log_dir.iterdir():
        # 날짜 이름 폴더만 대상으로 한다. 다른 파일/폴더는 건드리지 않는다.
        if not path.is_dir() or not _DATE_FOLDER_RE.match(path.name):
            continue
        try:
            folder_date = date.fromisoformat(path.name)
        except ValueError:
            continue
        # 폴더 날짜가 마감일보다 오래됐으면 폴더 + 로그 파일 + 내용을 전부 삭제한다.
        if folder_date <= cutoff:
            shutil.rmtree(path)
            deleted.append(path)
    return deleted


class KSTFormatter(logging.Formatter):
    """로그 시각을 한국 시간(KST)으로 출력하는 포맷터."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """로그 레코드의 타임스탬프를 KST 문자열로 바꾼다."""
        dt = datetime.fromtimestamp(record.created, KST)
        return dt.strftime(datefmt or _DATE_FORMAT)


class DateFolderFileHandler(logging.Handler):
    """
    날짜별 폴더에 오류 로그 파일을 쓰는 핸들러.

    저장 위치: logs/YYYY-MM-DD/error.log
    - 오늘 폴더가 없으면 만들고 error.log 에 이어 쓴다(append).
    - 자정이 지나 날짜가 바뀌면 이전 파일을 닫고 새 날짜 폴더로 갈아탄다.
    - 갈아탈 때 7일이 지난 폴더도 함께 정리한다.
    """

    terminator = "\n"

    def __init__(
        self,
        log_dir: Path,
        filename: str = "error.log",
        retention_days: int = RETENTION_DAYS,
    ) -> None:
        super().__init__()
        self.log_dir = log_dir          # logs/ 루트
        self.filename = filename        # 날짜 폴더 안에 만들 파일 이름
        self.retention_days = retention_days
        self.stream = None              # 현재 열려 있는 error.log 파일 객체
        self._current_date: date | None = None  # 지금 파일을 연 날짜

    def _path_for(self, day: date) -> Path:
        """해당 날짜 폴더를 만들고(없으면) error.log 경로를 반환한다."""
        folder = self.log_dir / day.isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        return folder / self.filename

    def _ensure_stream(self) -> None:
        """
        오늘 날짜의 로그 파일이 열려 있는지 확인한다.
        날짜가 바뀌었으면 이전 파일을 닫고 새 폴더의 error.log 를 연다.
        """
        day = today_kst()
        # 이미 오늘 파일을 열고 있으면 그대로 사용한다.
        if self.stream is not None and self._current_date == day:
            return
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        path = self._path_for(day)
        self.stream = path.open("a", encoding="utf-8")
        self._current_date = day
        # 새 날짜로 넘어갈 때 오래된 폴더도 함께 지운다.
        purge_old_log_folders(self.log_dir, self.retention_days, today=day)

    def emit(self, record: logging.LogRecord) -> None:
        """실제 로그 한 건을 파일에 쓴다. logger.error(...) 가 호출될 때 실행된다."""
        try:
            self._ensure_stream()
            assert self.stream is not None
            self.stream.write(self.format(record) + self.terminator)
            self.flush()
        except Exception:
            # 로그 기록 자체가 실패해도 앱은 죽지 않게 한다.
            self.handleError(record)

    def flush(self) -> None:
        """버퍼에 남은 내용을 디스크에 바로 반영한다. 서버가 꺼져도 내용이 남게 한다."""
        if self.stream is not None:
            self.stream.flush()

    def close(self) -> None:
        """핸들러가 끝날 때 열어 둔 파일을 닫는다."""
        try:
            if self.stream is not None:
                self.stream.close()
                self.stream = None
        finally:
            super().close()


def setup_logging() -> logging.Logger:
    """
    오류 로거를 한 번만 설정한다.

    - 콘솔: 터미널에도 오류 내용을 보여 준다.
    - 파일: logs/날짜/error.log 에 같은 내용을 저장한다.
    이미 핸들러가 붙어 있으면(재시작/중복 호출) 다시 붙이지 않는다.
    """
    logger = logging.getLogger(ERROR_LOGGER)
    if logger.handlers:
        return logger

    logger.setLevel(logging.ERROR)
    # 상위(root) 로거로 안 올라가게 해서 uvicorn 기본 로그와 섞이지 않게 한다.
    logger.propagate = False

    formatter = KSTFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # 개발할 때 터미널에서 바로 오류를 볼 수 있게 한다.
    console = logging.StreamHandler()
    console.setLevel(logging.ERROR)
    console.setFormatter(formatter)

    # 날짜 폴더 파일로도 남긴다.
    file_handler = DateFolderFileHandler(LOG_DIR)
    file_handler.setLevel(logging.ERROR)
    file_handler.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger
