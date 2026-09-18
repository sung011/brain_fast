"""풀이(review_node) 이력 SSE 이벤트 허브."""

from __future__ import annotations

import asyncio
import json
from typing import Any


class ReviewEventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        message = {"event": event, "data": data or {}}
        async with self._lock:
            targets = list(self._subscribers)
        for queue in targets:
            await queue.put(message)

    @staticmethod
    def format_sse(message: dict[str, Any]) -> str:
        event = message["event"]
        data = json.dumps(message["data"], ensure_ascii=False)
        return f"event: {event}\ndata: {data}\n\n"


review_events = ReviewEventHub()
