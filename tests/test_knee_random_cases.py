"""
무릎 MRI 여러 케이스 무작위 출제(GET /learning/knee/random)와 케이스별 기준 연결 시험. MSN-20261015-002 후속.

임시 PostgreSQL·합성 케이스 3개(24장·18장·31장)·임시 서버 폴더에서 **실제 라우터 코드**로 확인한다.
- 랜덤 단위는 이미지 한 장이 아니라 등록된 케이스(study) 하나다.
- '다른 문제'(exclude)는 다른 케이스가 있으면 현재 케이스를 주지 않는다.
- 케이스마다 장 수가 다르고, 기준은 그 케이스 자신의 것만 쓴다(기준 없는 케이스는 열람만, 채점 안 함·저장 0).
실행: python -m pytest tests/test_knee_random_cases.py   (pgserver, psycopg, pillow 필요)
"""
import collections
import json
import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

series_assets = import_module("class.knee.series_assets")
slide_refs = import_module("class.knee.slide_references")

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    import PIL  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False


@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg/pillow 가 없어 임시 DB 시험을 건너뜀")
class RandomCaseTests(unittest.TestCase):
    harness = None

    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        cls.harness = Harness(series=True, extra_cases=(18, 31))

    @classmethod
    def tearDownClass(cls):
        if cls.harness is not None:
            cls.harness.close()

    def setUp(self):
        self.h = self.harness
        self.client = TestClient(self.h.app, raise_server_exceptions=False)
        self.case1, self.case2 = self.h.extra
        self.all_ids = sorted([self.h.study_idx, self.case1["study_idx"], self.case2["study_idx"]])

    def login(self):
        self.assertEqual(self.client.post("/_login", params={"idx": self.h.user_idx}).status_code, 200)

    def random(self, exclude=None):
        params = {} if exclude is None else {"exclude": exclude}
        res = self.client.get("/learning/knee/random", params=params)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.headers["cache-control"], "no-store")
        return res.json()

    def grade(self, study, index, token, box):
        return self.client.post("/learning/roi-grade/knee", data={
            "study_idx": str(study), "roi_type": "box", "x": str(box[0]), "y": str(box[1]),
            "width": str(box[2]), "height": str(box[3]), "slice_index": str(index), "problem_token": token or ""})

    # ── 무작위 출제(케이스 단위) ──
    def test_random_requires_learner_login(self):
        self.assertEqual(self.client.get("/learning/knee/random").status_code, 401)

    def test_random_picks_whole_registered_cases_only(self):
        """후보 = 등록부에 있고 DB 에서 활성인 무릎 MRI 케이스. 등록 안 된 무릎 문제·뇌 문제는 나오지 않는다. 응답에는 번호와 후보 수뿐."""
        self.login()
        seen = collections.Counter()
        for _ in range(240):
            body = self.random()
            self.assertEqual(body["case_count"], 3)
            self.assertEqual(set(body), {"study_idx", "case_count", "same_as_excluded", "unavailable_reason"})
            seen[body["study_idx"]] += 1
        self.assertEqual(sorted(seen), self.all_ids)                  # 세 케이스 모두 나온다
        self.assertNotIn(self.h.unapproved_study_idx, seen)           # 열람 등록이 없는 무릎 문제
        self.assertNotIn(self.h.brain_study_idx, seen)

    def test_other_problem_never_repeats_current_case_when_another_exists(self):
        self.login()
        for current in self.all_ids:
            picks = {self.random(exclude=current)["study_idx"] for _ in range(60)}
            self.assertNotIn(current, picks)
            self.assertEqual(picks, set(self.all_ids) - {current})

    def test_single_case_pool_returns_same_case_with_flag_and_empty_pool_returns_null(self):
        self.login()
        only = {self.h.study_idx: series_assets.APPROVED_SERIES_ASSETS[self.h.study_idx]}
        with mock.patch.dict(series_assets.APPROVED_SERIES_ASSETS, only, clear=True):
            body = self.random(exclude=self.h.study_idx)
            self.assertEqual((body["study_idx"], body["case_count"], body["same_as_excluded"]), (self.h.study_idx, 1, True))
        with mock.patch.dict(series_assets.APPROVED_SERIES_ASSETS, {}, clear=True):
            body = self.random()
            self.assertEqual((body["study_idx"], body["case_count"], body["unavailable_reason"]), (None, 0, "no_registered_cases"))

    def test_withdrawn_or_deleted_or_stale_cases_leave_the_pool(self):
        self.login()
        withdrawn = [r.__class__(**{**r.__dict__, "status": "R"}) for r in series_assets.APPROVED_SERIES_ASSETS[self.case1["study_idx"]]]
        with mock.patch.dict(series_assets.APPROVED_SERIES_ASSETS, {self.case1["study_idx"]: withdrawn}):
            self.assertNotIn(self.case1["study_idx"], {self.random()["study_idx"] for _ in range(60)})
        stale = [r.__class__(**{**r.__dict__, "manifest_version": "old"}) for r in series_assets.APPROVED_SERIES_ASSETS[self.case2["study_idx"]]]
        with mock.patch.dict(series_assets.APPROVED_SERIES_ASSETS, {self.case2["study_idx"]: stale}):
            self.assertNotIn(self.case2["study_idx"], {self.random()["study_idx"] for _ in range(60)})
        from models.studyModel import StudyModel
        with self.h.Session() as db:
            db.get(StudyModel, self.case2["study_idx"]).del_yn = "Y"
            db.commit()
        try:
            self.assertEqual(self.random()["case_count"], 2)
        finally:
            with self.h.Session() as db:
                db.get(StudyModel, self.case2["study_idx"]).del_yn = "N"
                db.commit()

    # ── 케이스별 장 수·기준 ──
    def test_each_case_reports_its_own_real_slice_count(self):
        self.login()
        for idx, count in ((self.h.study_idx, 24), (self.case1["study_idx"], 18), (self.case2["study_idx"], 31)):
            body = self.client.get("/learning/knee/series", params={"study_idx": idx}).json()
            self.assertEqual((body["slice_count"], len(body["slices"])), (count, count), idx)
            self.assertEqual([s["slice_index"] for s in body["slices"]], list(range(count)))
        # 기준 없는 케이스는 가운데 장에서 시작, 대표 번호 비공개
        body = self.client.get("/learning/knee/series", params={"study_idx": self.case2["study_idx"]}).json()
        self.assertEqual((body["initial_slice_index"], body["representative_slice_index"]), (31 // 2, None))

    def test_case_without_reference_is_viewable_but_not_graded_and_not_saved(self):
        self.login()
        task = self.client.get("/learning/knee/task", params={"study_idx": self.case2["study_idx"]}).json()["knee_task"]
        self.assertFalse(task["grading_available"])
        self.assertEqual(task["unavailable_reason"], "no_reference_for_slice")
        self.assertIsNone(task["problem_token"])
        before = len(self.h.reviews())
        # 다른 케이스(메인)의 토큰·기준 박스로 제출해도 이 케이스는 채점하지 않는다
        main_token = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx}).json()["knee_task"]["problem_token"]
        res = self.grade(self.case2["study_idx"], self.h.rep, main_token, self.h.ref_box)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertFalse(res.json()["graded"])
        self.assertEqual(len(self.h.reviews()), before)

    def test_case_reference_is_its_own_slice_and_box_not_another_cases(self):
        self.login()
        task = self.client.get("/learning/knee/task", params={"study_idx": self.case1["study_idx"]}).json()["knee_task"]
        ref = self.case1["ref"]
        self.assertTrue(task["grading_available"])
        self.assertEqual((task["representative_slice_index"], task["slice_count"]), (ref.slice_no, 18))
        self.assertNotEqual(ref.slice_no, self.h.rep)
        token = task["problem_token"]
        # 메인 케이스의 기준 박스를 그려도 이 케이스의 기준(다른 박스)으로 계산된다 → 정답 아님
        wrong = self.grade(self.case1["study_idx"], ref.slice_no, token, self.h.ref_box).json()
        self.assertTrue(wrong["graded"])
        self.assertNotEqual(wrong["result"], "정답")
        # 이 케이스 자신의 기준 박스 → 정답
        right = self.grade(self.case1["study_idx"], ref.slice_no, token, (ref.x, ref.y, ref.width, ref.height)).json()
        self.assertEqual((right["graded"], right["result"]), (True, "정답"))
        # 기준 장이 아닌 장은 채점 안 함
        other = self.grade(self.case1["study_idx"], ref.slice_no + 1, token, (ref.x, ref.y, ref.width, ref.height)).json()
        self.assertFalse(other["graded"])
        # 메인 케이스 토큰을 이 케이스에 쓰면 채점하지 않는다(케이스 결속)
        main_token = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx}).json()["knee_task"]["problem_token"]
        crossed = self.grade(self.case1["study_idx"], ref.slice_no, main_token, (ref.x, ref.y, ref.width, ref.height)).json()
        self.assertFalse(crossed["graded"])

    def test_random_response_has_no_internal_values(self):
        self.login()
        dumped = json.dumps(self.random(), ensure_ascii=False)
        for secret in ("_knee_assets", "case1/", "png", "sha", "reference", "registrar"):
            self.assertNotIn(secret, dumped)


if __name__ == "__main__":
    unittest.main()
