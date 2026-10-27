"""
Codex 20차 FINDING-ASSET-020-01·02 회귀 시험: 영상 검증은 운영 제출에서 **생략될 수 없고**, 느린 영상 읽기 도중 승인·문제가 바뀌면 닫힌다.

020-01  공급원이 없거나 둘이거나 잘못된 설정이면 (이미 유효한 토큰으로 제출해도) 미채점·저장 0건. 잘못된 설정이 다른 공급원으로 조용히 넘어가지 않는다.
        "검증 생략"은 tests 가 명시로 주입할 때만 가능하다(운영 코드에 생략 허가가 없다).
020-02  영상 GET 과 제출 POST 는 영상을 읽는 동안(네트워크/디스크 대기) 승인 철회·교체·목록/문제 상태 변경이 일어나면
        반환·저장 전에 사용한 snapshot 과 현재 DB 를 다시 대조해 닫는다(영상 바이트·토큰·정답 비반환, 저장 0).
        이것은 읽는 동안의 변경을 닫는 단계이며 DB 트랜잭션·commit 원자성까지 보장하는 것은 아니다(대조 뒤 극히 짧은 틈은 남는다).
합성 계정·합성 영상·가짜 NAS·임시 PostgreSQL 만 쓴다. 실행: python -m pytest tests/test_knee_asset_closure.py
"""
import json
import os
import sys
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

from routers import kneeLearning

local = import_module("class.knee.local_assets")
nas = import_module("class.knee.nas_assets")

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    import PIL  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False


def set_verifier(function):
    """tests 가 검증 생략/대체를 **명시로** 주입하는 자리. (운영 코드에는 생략 허가가 없다.)"""
    hook = getattr(kneeLearning, "set_asset_verifier", None)
    if hook is not None:
        hook(function)


@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg/pillow 가 없어 임시 DB 시험을 건너뜀")
class ClosureBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        from test_knee_nas_assets import FakeNas
        cls.h = Harness()
        cls.nas = FakeNas()

    @classmethod
    def tearDownClass(cls):
        cls.nas.close()
        cls.h.close()

    def setUp(self):
        self.client = TestClient(self.h.app, raise_server_exceptions=False)
        self.h.clock.offset = 0
        self.tmp = tempfile.TemporaryDirectory(prefix="knee_closure_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        for path, data in self.h.png_by_path.items():
            target = self.folder / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.nas.routes.clear()
        for path, data in self.h.png_by_path.items():
            self.nas.serve_png(path, data)
        kneeLearning.set_asset_provider(None)
        set_verifier(None)                                       # 운영과 같은 상태에서 시작한다(생략 주입 없음)
        self.addCleanup(kneeLearning.set_asset_provider,
                        lambda ref, manifest: {"image_url": f"/_images/rep-{ref.reference_id}.png", "image_width": 320, "image_height": 384})
        self.addCleanup(set_verifier, lambda ref, manifest: None)   # 하네스의 기본(합성 시험용 명시 생략)으로 되돌린다
        env = mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": str(self.folder), "KNEE_NAS_IMAGE_BASE_URL": "", "KNEE_NAS_ALLOW_HTTP": ""})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._restore_world)
        self.assertEqual(self.client.post("/_login", params={"idx": self.h.user_idx}).status_code, 200)

    def _restore_world(self):
        from models.studyModel import StudyModel
        self.h.swap_reference(12, reference_id=7, version="v1")
        with self.h.Session() as db:
            row = db.get(StudyModel, self.h.study_idx)
            row.st_image, row.del_yn, row.st_part, row.st_modal = json.dumps(self.h.paths), "N", "4", "3"
            db.commit()

    def task(self):
        res = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["knee_task"]

    def grade(self, task):
        return self.client.post("/learning/roi-grade/knee", data={
            "study_idx": str(self.h.study_idx), "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4",
            "slice_index": str(task["representative_slice_index"]), "problem_token": task["problem_token"]})

    def assert_not_graded_and_not_saved(self, res, before, reasons=("study_image_unavailable", "asset_changed", "presentation_changed")):
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertFalse(body["graded"], body)
        self.assertIn(body["not_graded_reason"], reasons)
        self.assertIsNone(body["review_idx"])
        self.assertIsNone(body["result"])
        self.assertEqual(len(self.h.reviews()), before)


class SubmitVerificationCannotBeSkippedTests(ClosureBase):
    """FINDING-ASSET-020-01."""

    def test_baseline_with_one_valid_source_is_graded(self):
        task = self.task()
        before = len(self.h.reviews())
        self.assertTrue(self.grade(task).json()["graded"])
        self.assertEqual(len(self.h.reviews()), before + 1)

    def test_source_removed_after_the_task_closes_the_submit(self):
        task = self.task()
        before = len(self.h.reviews())
        with mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": ""}):
            self.assert_not_graded_and_not_saved(self.grade(task), before)

    def test_both_sources_configured_after_the_task_closes_the_submit(self):
        task = self.task()
        before = len(self.h.reviews())
        with mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": self.nas.base, "KNEE_NAS_ALLOW_HTTP": "1"}):
            self.assert_not_graded_and_not_saved(self.grade(task), before)

    def test_invalid_nas_setting_does_not_fall_back_to_the_valid_folder(self):
        for bad in ("http://nas.example.test/p", "ftp://x", "not a url", "https://user:pw@nas.example.test"):
            with self.subTest(nas=bad):
                with mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": bad}):
                    self.assertIsNone(kneeLearning._image_source())
                    res = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})
                    self.assertEqual(res.status_code, 503, res.text)              # 제시도 닫힌다(폴더로 넘어가지 않는다)

    def test_invalid_nas_setting_closes_a_submit_with_an_earlier_token(self):
        task = self.task()
        before = len(self.h.reviews())
        with mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": "http://nas.example.test/p"}):
            self.assert_not_graded_and_not_saved(self.grade(task), before)

    def test_valid_nas_with_a_missing_folder_does_not_fall_back_to_the_nas(self):
        env = {"KNEE_NAS_IMAGE_BASE_URL": self.nas.base, "KNEE_NAS_ALLOW_HTTP": "1", "KNEE_IMAGE_DIR": str(self.folder / "does-not-exist")}
        with mock.patch.dict(os.environ, env):
            self.assertIsNone(kneeLearning._image_source())
            self.assertEqual(self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx}).status_code, 503)

    def test_folder_removed_after_the_task_closes_the_submit(self):
        task = self.task()
        before = len(self.h.reviews())
        (self.folder / self.h.paths[12].lstrip("/")).unlink()
        self.assert_not_graded_and_not_saved(self.grade(task), before)

    def test_no_source_at_all_and_no_injected_verifier_never_grades(self):
        """공급원이 처음부터 없고 검증 생략 주입도 없으면, 유효한 토큰이 있어도 채점·저장이 없다."""
        task = self.task()
        before = len(self.h.reviews())
        with mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": "", "KNEE_NAS_IMAGE_BASE_URL": ""}):
            for _ in range(2):                                               # 같은 토큰 재사용도 마찬가지
                self.assert_not_graded_and_not_saved(self.grade(task), before)

    def test_skipping_is_only_possible_by_explicit_test_injection(self):
        task = self.task()
        set_verifier(lambda ref, manifest: None)                              # tests 안의 명시 주입(운영 코드 경로 아님)
        with mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": ""}):
            self.assertTrue(self.grade(task).json()["graded"])

    def test_a_configured_source_always_wins_over_an_injected_skip(self):
        task = self.task()
        set_verifier(lambda ref, manifest: None)                              # 생략을 주입해 두어도
        (self.folder / self.h.paths[12].lstrip("/")).write_bytes(b"\x89PNG tampered")
        before = len(self.h.reviews())
        self.assert_not_graded_and_not_saved(self.grade(task), before)       # 설정된 공급원의 실제 검증이 우선한다


class ChangeWhileReadingTests(ClosureBase):
    """FINDING-ASSET-020-02 — 영상을 읽는 동안(느린 NAS/디스크) 승인·문제가 바뀌는 경우."""

    def mutations(self):
        from models.studyModel import StudyModel

        def edit_study(**fields):
            def apply():
                with self.h.Session() as db:
                    row = db.get(StudyModel, self.h.study_idx)
                    for key, value in fields.items():
                        setattr(row, key, value)
                    db.commit()
            return apply
        return {
            "approval withdrawn": lambda: self.h.swap_reference(0, remove=True),
            "approval replaced (other slice)": lambda: self.h.swap_reference(13, reference_id=8, version="v2"),
            "approval replaced (same slice, new reference)": lambda: self.h.swap_reference(12, reference_id=9, version="v9"),
            "slide list changed": edit_study(st_image=json.dumps(self.h.paths[:-1])),
            "study deleted": edit_study(del_yn="Y"),
            "study body part changed": edit_study(st_part="1"),
            "study modality changed": edit_study(st_modal="2"),
        }

    def run_with_change_during_read(self, change, action):
        """폴더 공급원이 영상을 읽은 직후(바이트는 정상) 변경을 일으키고, 그 상태로 action 을 실행한다."""
        real_get = local.LocalImageSource.get
        armed = {"on": False}

        def slow_get(source, path, expected):
            image = real_get(source, path, expected)
            if armed["on"]:
                armed["on"] = False
                change()                                                      # 읽는 동안 일어난 변경
            return image
        with mock.patch.object(local.LocalImageSource, "get", slow_get):
            armed["on"] = True
            return action()

    def test_image_get_is_closed_when_the_state_changes_while_reading(self):
        url = self.task()["image_url"]
        for name, change in self.mutations().items():
            with self.subTest(change=name):
                res = self.run_with_change_during_read(change, lambda: self.client.get(url))
                self.assertEqual(res.status_code, 409, f"{name}: {res.status_code}")
                self.assertNotEqual(res.headers.get("content-type"), "image/png")
                self.assertNotIn(self.h.png_by_path[self.h.paths[12]][:16], res.content)   # 영상 바이트 비반환
                self._restore_world()
                url = self.task()["image_url"]

    def test_submit_is_not_graded_or_saved_when_the_state_changes_while_reading(self):
        for name, change in self.mutations().items():
            with self.subTest(change=name):
                task = self.task()
                before = len(self.h.reviews())
                res = self.run_with_change_during_read(change, lambda: self.grade(task))
                self.assert_not_graded_and_not_saved(res, before, reasons=("presentation_changed",))
                self._restore_world()

    def test_after_the_change_settles_the_old_token_still_cannot_grade_the_new_state(self):
        task = self.task()
        self.h.swap_reference(13, reference_id=8, version="v2")
        before = len(self.h.reviews())
        res = self.grade(task)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["graded"])
        self.assertEqual(len(self.h.reviews()), before)

    def test_stable_state_is_unaffected(self):
        url = self.task()["image_url"]
        res = self.run_with_change_during_read(lambda: None, lambda: self.client.get(url))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, self.h.rep_png)
        task = self.task()
        before = len(self.h.reviews())
        self.assertTrue(self.run_with_change_during_read(lambda: None, lambda: self.grade(task)).json()["graded"])
        self.assertEqual(len(self.h.reviews()), before + 1)


@unittest.skipUnless(HAVE_DB_TOOLS, "임시 DB 도구 없음")
class NasChangeWhileReadingTests(ClosureBase):
    """NAS 공급원(네트워크 대기)에서도 같은 규칙."""

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": "", "KNEE_NAS_IMAGE_BASE_URL": self.nas.base, "KNEE_NAS_ALLOW_HTTP": "1"})
        env.start()
        self.addCleanup(env.stop)
        source = nas.source_from_settings()
        source.clear_cache()
        self.addCleanup(source.clear_cache)

    def test_nas_fetch_then_withdrawal_closes_get_and_submit(self):
        source = nas.source_from_settings()
        task = self.task()                                                    # 캐시에 검증된 바이트가 생긴다
        url = task["image_url"]
        real_get = nas.NasImageSource.get

        def slow(source_, path, expected):
            image = real_get(source_, path, expected)
            self.h.swap_reference(0, remove=True)
            return image
        with mock.patch.object(nas.NasImageSource, "get", slow):
            res = self.client.get(url)
        self.assertEqual(res.status_code, 409)
        self.assertNotIn(self.h.rep_png[:16], res.content)
        self._restore_world()
        source.clear_cache()
        task = self.task()
        before = len(self.h.reviews())
        with mock.patch.object(nas.NasImageSource, "get", slow):
            res = self.grade(task)
        self.assert_not_graded_and_not_saved(res, before, reasons=("presentation_changed",))


if __name__ == "__main__":
    unittest.main()
