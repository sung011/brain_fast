"""오늘의 학습 현황: 부위별 시간·문제 수·정답률."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db import get_db
from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from routers.commonness import router
from services.learningServices import study_minutes, summarize_today_parts, today_learning


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    StudyModel.__table__.create(engine)
    ReviewNodeModel.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    session.info["engine"] = engine
    return session


def _close(db) -> None:
    engine = db.info.get("engine")
    db.close()
    if engine is not None:
        engine.dispose()


def _study(db, *, part: str) -> StudyModel:
    row = StudyModel(del_yn="N", st_part=part, st_modal="2", st_disease="병명", state="N")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _review(db, *, user_idx: int, study_idx: int, solution: str, at: datetime) -> None:
    row = ReviewNodeModel(
        del_yn="N",
        rn_u_idx=user_idx,
        rn_s_idx=study_idx,
        rn_solution=solution,
        state="N",
        created_at=at,
    )
    db.add(row)
    db.commit()


class StudyMinutesTests(unittest.TestCase):
    def test_single_submission_is_zero(self) -> None:
        now = datetime(2026, 10, 21, 15, 0, 0)
        self.assertEqual(study_minutes([now]), 0)

    def test_span_rounds_to_minutes(self) -> None:
        start = datetime(2026, 10, 21, 15, 0, 0)
        end = start + timedelta(minutes=20)
        self.assertEqual(study_minutes([end, start]), 20)


class TodayLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _session()
        self.now = datetime(2026, 10, 21, 18, 0, 0)
        brain = _study(self.db, part="1")
        chest = _study(self.db, part="2")
        start = datetime(2026, 10, 21, 15, 0, 0)
        _review(self.db, user_idx=3, study_idx=brain.idx, solution="C", at=start)
        _review(
            self.db,
            user_idx=3,
            study_idx=brain.idx,
            solution="W",
            at=start + timedelta(minutes=10),
        )
        _review(
            self.db,
            user_idx=3,
            study_idx=brain.idx,
            solution="C",
            at=start + timedelta(minutes=20),
        )
        _review(self.db, user_idx=3, study_idx=chest.idx, solution="C", at=start)
        _review(
            self.db,
            user_idx=3,
            study_idx=chest.idx,
            solution="H",
            at=start + timedelta(minutes=25),
        )
        _review(
            self.db,
            user_idx=9,
            study_idx=brain.idx,
            solution="C",
            at=start,
        )
        _review(
            self.db,
            user_idx=3,
            study_idx=brain.idx,
            solution="C",
            at=start - timedelta(days=1),
        )

    def tearDown(self) -> None:
        _close(self.db)

    def test_groups_today_by_part(self) -> None:
        result = today_learning(self.db, 3, now=self.now)
        self.assertEqual(result["date"], "2026-10-21")
        by_part = {item["st_part"]: item for item in result["items"]}
        self.assertEqual(list(by_part), ["1", "2"])
        self.assertEqual(by_part["1"]["part_label"], "뇌")
        self.assertEqual(by_part["1"]["study_minutes"], 20)
        self.assertEqual(by_part["1"]["problem_count"], 3)
        self.assertEqual(by_part["1"]["correct_rate"], 67)
        self.assertEqual(by_part["2"]["part_label"], "흉부")
        self.assertEqual(by_part["2"]["study_minutes"], 25)
        self.assertEqual(by_part["2"]["problem_count"], 2)
        self.assertEqual(by_part["2"]["correct_rate"], 50)
        self.assertNotIn("3", by_part)
        self.assertNotIn("4", by_part)

    def test_api_omits_parts_without_today(self) -> None:
        app = FastAPI()
        app.include_router(router)

        def _db():
            yield self.db

        app.dependency_overrides[get_db] = _db
        response = TestClient(app).get("/learning/today", params={"user_idx": 3})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual([item["st_part"] for item in body["items"]], ["1", "2"])
        self.assertEqual(body["items"][0]["problem_count"], 3)


class SummarizeEmptyTests(unittest.TestCase):
    def test_empty_rows_return_no_parts(self) -> None:
        self.assertEqual(summarize_today_parts([]), [])
