"""
시도 횟수 제한 (메모리 기반 슬라이딩 윈도우).

키(IP, 아이디 등)마다 최근 window_seconds 동안의 시도 시각을 보관하고,
max_attempts 를 넘으면 거부한다.

주의: 프로세스 메모리에만 있으므로 서버를 재시작하면 초기화되고,
uvicorn 워커가 여러 개면 워커마다 따로 센다.
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock


class RateLimiter:
    """키별 시도 횟수를 세는 간단한 제한기."""

    def __init__(self, max_attempts: int, window_seconds: float) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def hit(self, key: str, now: float | None = None) -> bool:
        """시도를 1회 기록한다. 허용이면 True, 제한을 넘었으면 False(기록하지 않음)."""
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.max_attempts:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:
                self._prune(cutoff)
            return True

    def _prune(self, cutoff: float) -> None:
        """오래된 키를 지워 메모리가 계속 늘지 않게 한다."""
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]

    def reset(self) -> None:
        """모든 기록을 지운다. 테스트용."""
        with self._lock:
            self._hits.clear()
