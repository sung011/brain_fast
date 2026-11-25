"""
무릎 MRI 학습 문제 목록(GET /learning/problems?st_part=4&st_modal=3)이
채점 가능한 케이스만 남기는지 확인한다.

- stylesheet 전용 경로처럼 KNEE_ASSET_ROOTS 밖이면 목록에서 빠진다.
- 허용 루트 + 승인표 resolve ok 인 문제만 남는다.
- 흉부 등 다른 부위 목록은 이 필터와 무관하다.
"""
from __future__ import annotations

import json
import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from temp_pg import HAVE_DB_TOOLS
    import psycopg  # noqa: F401
    import PIL  # noqa: F401
    if not HAVE_DB_TOOLS:
        raise ImportError("temp pg backend missing")
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False


@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg/pillow 가 없어 임시 DB 시험을 건너뜀")
class KneeProblemsListTests(unittest.TestCase):
    harness = None

    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        cls.harness = Harness()

    @classmethod
    def tearDownClass(cls):
        if cls.harness is not None:
            cls.harness.close()

    def setUp(self):
        self.h = self.harness
        self.client = TestClient(self.h.app, raise_server_exceptions=False)
        StudyModel = import_module("models.studyModel").StudyModel
        self.db = self.h.Session()
        self.addCleanup(self.db.close)

        self.gradeable_idx = self.h.study_idx

        bad = StudyModel(
            st_part="4",
            st_modal="3",
            st_disease="ACL",
            st_image=json.dumps([
                "/stylesheets/assets/Knee/MRI/aaa_01_case-x.png",
                "/stylesheets/assets/Knee/MRI/bbb_02_case-x.png",
            ]),
            del_yn="N",
            state="N",
        )
        self.db.add(bad)
        self.db.commit()
        self.db.refresh(bad)
        self.bad_idx = bad.idx
        self.addCleanup(self._delete_study, self.bad_idx)

        chest = StudyModel(
            st_part="2",
            st_modal="1",
            st_disease="기흉",
            st_image="/stylesheets/assets/thorax/X-ray/x.png",
            del_yn="N",
            state="N",
        )
        self.db.add(chest)
        self.db.commit()
        self.db.refresh(chest)
        self.chest_idx = chest.idx
        self.addCleanup(self._delete_study, self.chest_idx)

    def _delete_study(self, idx: int):
        StudyModel = import_module("models.studyModel").StudyModel
        row = self.db.get(StudyModel, idx)
        if row is not None:
            self.db.delete(row)
            self.db.commit()

    def test_knee_mri_list_keeps_only_gradeable(self):
        body = self.client.get("/learning/problems", params={"st_part": "4", "st_modal": "3"}).json()
        idxs = [item["idx"] for item in body["items"]]
        self.assertIn(self.gradeable_idx, idxs)
        self.assertNotIn(self.bad_idx, idxs)
        self.assertNotIn(self.chest_idx, idxs)

    def test_chest_list_unchanged_by_knee_filter(self):
        body = self.client.get("/learning/problems", params={"st_part": "2", "st_modal": "1"}).json()
        idxs = [item["idx"] for item in body["items"]]
        self.assertIn(self.chest_idx, idxs)
        self.assertNotIn(self.gradeable_idx, idxs)

    def test_gradeable_helper_matches_resolve(self):
        learning = import_module("services.learningServices")
        StudyModel = import_module("models.studyModel").StudyModel
        good = self.db.get(StudyModel, self.gradeable_idx)
        bad = self.db.get(StudyModel, self.bad_idx)
        self.assertTrue(learning._knee_mri_grading_ready(good))
        self.assertFalse(learning._knee_mri_grading_ready(bad))

    def test_removing_approval_drops_from_list(self):
        slide_refs = import_module("class.knee.slide_references")
        with mock.patch.dict(slide_refs.APPROVED_SLIDE_REFERENCES, {}, clear=True):
            body = self.client.get("/learning/problems", params={"st_part": "4", "st_modal": "3"}).json()
        self.assertEqual(body["items"], [])


if __name__ == "__main__":
    unittest.main()
