"""팝업 등록이 SSE(popup_created)를 보내는지 확인한다."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import get_db
from routers import admin as admin_router
from routers.admin import router
from utils.adminAuth import admin_session_guard
from utils.popup_sse import popup_events


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[admin_session_guard] = lambda: {"idx": 1}

    def _db():
        yield object()

    app.dependency_overrides[get_db] = _db
    return app


class PopupCreateSseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_app())
        self._nas = admin_router.nas_service_mod.nas_service
        self._create = admin_router.popup_service.create_popup
        self.published: list[tuple] = []

        async def upload_bytes(data: bytes, name: str, remote_dir: str = ""):
            return {"remote_path": f"{remote_dir}/{name}"}

        admin_router.nas_service_mod.nas_service = SimpleNamespace(
            enabled=True,
            upload_bytes=upload_bytes,
        )

        def create_popup(db, **kwargs):
            return SimpleNamespace(
                idx=7,
                pp_title=kwargs.get("pp_title"),
                pp_image=kwargs.get("pp_image"),
                pp_link=kwargs.get("pp_link"),
                pp_sort=kwargs.get("pp_sort") or 0,
                state=(kwargs.get("state") or "N"),
            )

        admin_router.popup_service.create_popup = create_popup

        original = popup_events.publish

        async def capture(event, data=None):
            self.published.append((event, data or {}))
            await original(event, data)

        self._publish = popup_events.publish
        popup_events.publish = capture

    def tearDown(self) -> None:
        admin_router.nas_service_mod.nas_service = self._nas
        admin_router.popup_service.create_popup = self._create
        popup_events.publish = self._publish

    def test_create_publishes_popup_created(self) -> None:
        response = self.client.post(
            "/admin/popup",
            data={"pp_title": "오픈", "pp_sort": "1", "state": "N"},
            files={"file": ("banner.png", b"png", "image/png")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["idx"], 7)
        self.assertEqual(len(self.published), 1)
        event, data = self.published[0]
        self.assertEqual(event, "popup_created")
        self.assertEqual(data["idx"], 7)
        self.assertEqual(data["pp_title"], "오픈")
        self.assertEqual(data["state"], "N")

    def test_stream_route_is_registered(self) -> None:
        paths = {getattr(route, "path", None) for route in router.routes}
        self.assertIn("/admin/popups/stream", paths)


class PopupEventHubTests(unittest.TestCase):
    def test_publish_reaches_subscriber(self) -> None:
        async def run():
            queue = await popup_events.subscribe()
            try:
                await popup_events.publish("popup_created", {"idx": 3, "pp_title": "배너"})
                return await asyncio.wait_for(queue.get(), timeout=1)
            finally:
                await popup_events.unsubscribe(queue)

        message = asyncio.run(run())
        self.assertEqual(message["event"], "popup_created")
        self.assertEqual(message["data"]["idx"], 3)
        framed = popup_events.format_sse(message)
        self.assertIn("event: popup_created", framed)
        self.assertIn('"pp_title": "배너"', framed)
