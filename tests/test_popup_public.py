"""GET /popups 는 노출이면서 노출 기간 안인 팝업만 돌려준다."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import get_db
from models.popupModel import PopupModel
from repositories.popupRepositories import list_public_active
from routers.commonness import router


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    PopupModel.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.info["engine"] = engine
    return session


def _close(db) -> None:
    engine = db.info.get("engine")
    db.close()
    if engine is not None:
        engine.dispose()


def _add(db, **kwargs) -> PopupModel:
    row = PopupModel(
        del_yn=kwargs.pop("del_yn", "N"),
        pp_title=kwargs.pop("pp_title", "팝업"),
        pp_image=kwargs.pop("pp_image", "/stylesheets/assets/popup/a.png"),
        pp_link=kwargs.pop("pp_link", None),
        pp_sort=kwargs.pop("pp_sort", 0),
        state=kwargs.pop("state", "N"),
        start_at=kwargs.pop("start_at", None),
        end_at=kwargs.pop("end_at", None),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class PublicPopupFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _session()
        self.now = datetime(2026, 10, 20, 15, 0, 0)

    def tearDown(self) -> None:
        _close(self.db)

    def test_only_exposed_and_inside_window(self) -> None:
        inside = _add(
            self.db,
            pp_title="기간 안",
            pp_sort=1,
            state="N",
            start_at=self.now - timedelta(hours=1),
            end_at=self.now + timedelta(hours=1),
        )
        _add(
            self.db,
            pp_title="숨김",
            state="S",
            start_at=self.now - timedelta(hours=1),
            end_at=self.now + timedelta(hours=1),
        )
        _add(
            self.db,
            pp_title="시작 전",
            state="N",
            start_at=self.now + timedelta(minutes=1),
            end_at=self.now + timedelta(hours=2),
        )
        _add(
            self.db,
            pp_title="종료 후",
            state="N",
            start_at=self.now - timedelta(hours=2),
            end_at=self.now - timedelta(minutes=1),
        )
        _add(
            self.db,
            pp_title="삭제",
            del_yn="Y",
            state="N",
            start_at=self.now - timedelta(hours=1),
            end_at=self.now + timedelta(hours=1),
        )
        open_ended = _add(self.db, pp_title="상시", pp_sort=0, state="N")

        rows = list_public_active(self.db, now=self.now)
        self.assertEqual([row.idx for row in rows], [open_ended.idx, inside.idx])

    def test_bounds_are_inclusive(self) -> None:
        row = _add(
            self.db,
            state="N",
            start_at=self.now,
            end_at=self.now,
        )
        rows = list_public_active(self.db, now=self.now)
        self.assertEqual([item.idx for item in rows], [row.idx])


class PublicPopupApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _session()
        app = FastAPI()
        app.include_router(router)

        def _db():
            yield self.db

        app.dependency_overrides[get_db] = _db
        self.client = TestClient(app)
        now = datetime.now().replace(microsecond=0)
        self.kept = _add(
            self.db,
            pp_title="노출",
            pp_sort=2,
            state="N",
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            pp_link="https://example.com",
        )
        _add(
            self.db,
            pp_title="숨김",
            state="S",
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
        )

    def tearDown(self) -> None:
        _close(self.db)

    def test_get_popups_returns_only_active(self) -> None:
        response = self.client.get("/popups")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["idx"], self.kept.idx)
        self.assertEqual(body[0]["pp_title"], "노출")
        self.assertEqual(body[0]["state"], "N")
        self.assertEqual(body[0]["pp_link"], "https://example.com")
        self.assertTrue(body[0]["pp_image_url"])
