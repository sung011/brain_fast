"""
로그 기능 테스트.

1) 7일이 지난 날짜 폴더가 삭제되는지
2) 오류가 오늘 날짜 폴더의 error.log 에 쓰이는지
3) 실제 앱에서 서버 오류가 로그 파일에 내용까지 남는지
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
import unittest

from logging_setup import DateFolderFileHandler, KSTFormatter, purge_old_log_folders, today_kst


class LogRetentionTests(unittest.TestCase):
    def test_deletes_folders_older_than_7_days(self) -> None:
        """오늘이 8/25일 때 8/18 이전 폴더(파일 포함)는 지우고, 최근 7일 폴더는 남긴다."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            today = date(2026, 8, 25)
            keep = ["2026-08-19", "2026-08-24", "2026-08-25"]
            delete = ["2026-08-17", "2026-08-18"]
            for name in keep + delete:
                folder = log_dir / name
                folder.mkdir()
                (folder / "error.log").write_text("old error\n", encoding="utf-8")

            deleted = purge_old_log_folders(log_dir, retention_days=7, today=today)

            self.assertEqual(
                sorted(path.name for path in deleted),
                delete,
            )
            for name in keep:
                self.assertTrue((log_dir / name / "error.log").exists())
            for name in delete:
                self.assertFalse((log_dir / name).exists())

    def test_writes_error_into_today_folder(self) -> None:
        """logger.error() 한 줄이 logs/오늘날짜/error.log 에 실제로 기록되는지 확인한다."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            handler = DateFolderFileHandler(log_dir)
            handler.setFormatter(KSTFormatter("%(message)s"))
            logger = logging.getLogger("test.date_folder")
            logger.handlers.clear()
            logger.addHandler(handler)
            logger.setLevel(logging.ERROR)
            logger.propagate = False

            logger.error("테스트 오류 내용")
            handler.close()

            today_folder = log_dir / today_kst().isoformat()
            log_file = today_folder / "error.log"
            self.assertTrue(log_file.exists())
            self.assertIn("테스트 오류 내용", log_file.read_text(encoding="utf-8"))

    def test_app_writes_server_error_to_dated_log(self) -> None:
        """GET /debug/error 로 500을 내고, 날짜 폴더 로그에 RuntimeError 내용이 남는지 확인한다."""
        from fastapi.testclient import TestClient

        from logging_setup import LOG_DIR, today_kst
        from main import app

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/debug/error")

        self.assertEqual(response.status_code, 500)
        log_file = LOG_DIR / today_kst().isoformat() / "error.log"
        self.assertTrue(log_file.exists())
        content = log_file.read_text(encoding="utf-8")
        self.assertIn("RuntimeError", content)
        self.assertIn("테스트용 서버 오류입니다", content)


if __name__ == "__main__":
    unittest.main()
