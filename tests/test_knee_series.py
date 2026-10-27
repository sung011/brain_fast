"""
무릎 MRI 여러 장 열람 API(GET /learning/knee/series, 장별 영상)와 대표 1장 채점의 연결 시험. 설계 MSN-20261015-002.

임시 PostgreSQL·합성 24장·임시 서버 폴더(운영과 같은 local_assets 공급원)에서 **실제 라우터 코드**로 확인한다
(시험 전용 로그인·합성 영상·임시 등록부는 tests/knee_screen_harness.py 안에만 있다. 운영 코드에는 없다).
실행: python -m pytest tests/test_knee_series.py   (pgserver, psycopg, pillow 필요)
"""
import hashlib
import json
import os
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
slice_manifest = import_module("class.knee.slice_manifest")

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    import PIL  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False


class RegistryRulesTests(unittest.TestCase):
    """등록부 규칙(DB·서버 없음): 기본 비어 있음, 목록과 어긋나면 없음, 둘 이상이면 ambiguous, series_id 의 성질."""

    def setUp(self):
        self.paths = [f"/_knee_assets/study/{i:03d}.png" for i in range(3)]
        self.manifest = slice_manifest.build_manifest(json.dumps(self.paths), ("/_knee_assets/",))
        self.hashes = tuple(hashlib.sha256(bytes([i])).hexdigest() for i in range(3))

    def row(self, **over):
        fields = dict(
            study_idx=5, manifest_version=self.manifest.version, slice_set_sha256=self.manifest.list_sha256,
            slice_sha256=self.hashes, slice_sizes=((8, 8),) * 3, kind="synthetic", notice="합성", version="v1",
            registered_by="registrar-000", registered_at="2026-10-15T00:00:00",
        )
        fields.update(over)
        return series_assets.SeriesAssets(**fields)

    def resolve(self, rows):
        with mock.patch.dict(series_assets.APPROVED_SERIES_ASSETS, {5: rows}, clear=True):
            return series_assets.resolve(5, self.manifest)

    def test_registry_holds_exactly_the_registered_data_file_rows_and_nothing_else(self):
        """등록부는 사람이 채우는 데이터 파일(series_registered_data.py)의 행과 정확히 같다. 데이터 파일이 비면 등록부도 비어 있다."""
        data = import_module("class.knee.series_registered_data")
        self.assertEqual(sorted(series_assets.APPROVED_SERIES_ASSETS), sorted({row["study_idx"] for row in data.REGISTERED_ROWS}))
        self.assertEqual(series_assets.resolve(5, self.manifest).status, "none")   # 등록되지 않은 문제는 없음

    def test_each_registered_row_is_self_consistent_with_its_own_path_list(self):
        """실제 등록 행은 자기 경로 목록(paths)에서 만든 목록(manifest)과 어긋나지 않는다(해시 수·치수·목록 버전·종류·출처 문구)."""
        data = import_module("class.knee.series_registered_data")
        for row in data.REGISTERED_ROWS:
            manifest = slice_manifest.build_manifest(json.dumps(row["paths"], ensure_ascii=False), (row["paths"][0].rsplit("/", 1)[0] + "/",))
            self.assertEqual(len(row["slice_sha256"]), manifest.count)
            registered = series_assets.APPROVED_SERIES_ASSETS[row["study_idx"]]
            self.assertEqual(len(registered), 1)
            with mock.patch.dict(series_assets.APPROVED_SERIES_ASSETS, {row["study_idx"]: registered}, clear=True):
                self.assertEqual(series_assets.resolve(row["study_idx"], manifest).status, "ok", row["study_idx"])
            self.assertIn(row["kind"], series_assets.KINDS)
            self.assertTrue(row["notice"].strip())

    def test_valid_row_resolves_and_withdrawn_or_double_rows_do_not(self):
        self.assertEqual(self.resolve([self.row()]).status, "ok")
        self.assertEqual(self.resolve([self.row(status="R")]).status, "none")
        self.assertEqual(self.resolve([self.row(), self.row(version="v2")]).status, "ambiguous")

    def test_stale_or_malformed_rows_are_treated_as_missing(self):
        bad = {
            "study": dict(study_idx=6), "manifest": dict(manifest_version="other"), "list": dict(slice_set_sha256="0" * 64),
            "hash count": dict(slice_sha256=self.hashes[:2]), "hash format": dict(slice_sha256=("x",) * 3),
            "size count": dict(slice_sizes=((8, 8),) * 2), "size zero": dict(slice_sizes=((0, 8),) * 3),
            "size bool": dict(slice_sizes=((True, 8),) * 3), "size big": dict(slice_sizes=((99999, 8),) * 3),
            "kind": dict(kind="demo"), "empty notice": dict(notice=" "), "list not tuple": dict(slice_sha256=list(self.hashes)),
        }
        for name, over in bad.items():
            self.assertEqual(self.resolve([self.row(**over)]).status, "none", name)

    def test_series_id_is_stable_opaque_and_follows_every_slice(self):
        key = b"k" * 40
        base = series_assets.series_id(key, study_idx=5, manifest=self.manifest, assets=self.row())
        self.assertRegex(base, r"^[0-9a-f]{20}$")
        self.assertEqual(base, series_assets.series_id(key, study_idx=5, manifest=self.manifest, assets=self.row(notice="다른 문구")))
        changed = list(self.hashes)
        changed[1] = hashlib.sha256(b"x").hexdigest()
        self.assertNotEqual(base, series_assets.series_id(key, study_idx=5, manifest=self.manifest, assets=self.row(slice_sha256=tuple(changed))))
        self.assertNotEqual(base, series_assets.series_id(b"z" * 40, study_idx=5, manifest=self.manifest, assets=self.row()))
        self.assertNotEqual(base, series_assets.series_id(key, study_idx=6, manifest=self.manifest, assets=self.row()))
        for leak in self.paths + list(self.hashes):
            self.assertNotIn(leak[:12], base)


@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg/pillow 가 없어 임시 DB 시험을 건너뜀")
class SeriesApiTests(unittest.TestCase):
    harness = None

    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        cls.harness = Harness(series=True)

    @classmethod
    def tearDownClass(cls):
        if cls.harness is not None:
            cls.harness.close()

    def setUp(self):
        self.h = self.harness
        self.client = TestClient(self.h.app, raise_server_exceptions=False)
        series_assets.APPROVED_SERIES_ASSETS[self.h.study_idx] = [self.h.series_row()]
        for index in list(self.h.broken):
            self.h.fix_slice(index)
        self.h.clock.offset = 0

    def login(self, idx=None, verified=True):
        self.assertEqual(self.client.post("/_login", params={"idx": idx or self.h.user_idx, "verified": str(verified).lower()}).status_code, 200)

    def series(self, study=None):
        return self.client.get("/learning/knee/series", params={"study_idx": study or self.h.study_idx})

    def task(self):
        res = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["knee_task"]

    def grade(self, index, token, box=(0.3, 0.3, 0.4, 0.4)):
        return self.client.post("/learning/roi-grade/knee", data={
            "study_idx": str(self.h.study_idx), "roi_type": "box", "x": str(box[0]), "y": str(box[1]),
            "width": str(box[2]), "height": str(box[3]), "slice_index": str(index), "problem_token": token})

    # ── 열기 ──
    def test_doors_are_closed_without_login_and_for_other_studies(self):
        self.assertEqual(self.series().status_code, 401)
        self.login(verified=False)
        self.assertEqual(self.series().status_code, 403)
        self.login()
        for idx in (self.h.brain_study_idx, 99999):
            self.assertEqual(self.series(idx).status_code, 404, idx)
        self.assertEqual(self.client.get("/learning/knee/series").status_code, 422)

    def test_series_lists_all_slices_in_order_without_internal_values(self):
        self.login()
        res = self.series()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["cache-control"], "no-store")
        body = res.json()
        self.assertTrue(body["series_available"])
        self.assertEqual(body["slice_count"], 24)
        self.assertEqual([s["slice_index"] for s in body["slices"]], list(range(24)))
        self.assertTrue(all((s["image_width"], s["image_height"]) == (320, 384) for s in body["slices"]))
        self.assertTrue(all(s["image_url"] == f"/learning/knee/series/{self.h.study_idx}/{body['series_id']}/image/{s['slice_index']}" for s in body["slices"]))
        self.assertEqual((body["source_kind"], body["initial_slice_index"], body["representative_slice_index"], body["grading_enabled"]), ("synthetic", self.h.rep, self.h.rep, True))
        dumped = json.dumps(body, ensure_ascii=False)
        for secret in (self.h.paths[0], "_knee_assets", self.h.rep_sha, "reference", "reviewer", "registrar", "ACL", "synthetic dataset", "0.3,"):
            self.assertNotIn(secret, dumped, secret)

    def test_no_representative_shows_middle_slice_and_hides_representative_number(self):
        self.login()
        with mock.patch.dict(slide_refs.APPROVED_SLIDE_REFERENCES, {}, clear=True):
            body = self.series().json()
        self.assertEqual((body["initial_slice_index"], body["representative_slice_index"]), (12, None))   # 24 // 2
        self.assertEqual(len(body["slices"]), 24)    # 기준이 없어도 등록된 시리즈는 탐색할 수 있다

    def test_browsing_works_while_grading_is_switched_off_but_grading_does_not(self):
        self.login()
        with mock.patch.dict(os.environ, {"KNEE_ROI_GRADE_ENABLED": "0"}):
            body = self.series().json()
            self.assertTrue(body["series_available"])
            self.assertFalse(body["grading_enabled"])
            self.assertIsNone(body["representative_slice_index"])     # 꺼져 있으면 대표 번호를 알리지 않는다
            self.assertEqual(self.client.get(body["slices"][3]["image_url"]).status_code, 200)   # 영상은 열린다
            self.assertEqual(self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx}).status_code, 503)   # 채점 제시는 닫혀 있다

    def test_unregistered_or_unusable_list_gives_reason_without_urls(self):
        self.login()
        series_assets.APPROVED_SERIES_ASSETS.pop(self.h.study_idx)
        body = self.series().json()
        self.assertEqual((body["series_available"], body["unavailable_reason"]), (False, "series_not_registered"))
        self.assertNotIn("slices", body)
        body = self.series(self.h.unapproved_study_idx).json()    # 같은 무릎 MRI 문제지만 열람 등록이 없다
        self.assertEqual(body["unavailable_reason"], "series_not_registered")

    def test_registered_but_without_image_source_or_key_is_503(self):
        self.login()
        with mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": ""}):
            self.assertEqual(self.series().status_code, 503)
        with mock.patch.dict(os.environ, {"KNEE_PROBLEM_TOKEN_KEY": ""}):
            self.assertEqual(self.series().status_code, 503)

    # ── 장별 영상 ──
    def test_each_slice_serves_its_own_verified_bytes(self):
        self.login()
        body = self.series().json()
        for index in (0, 5, 12, 23):
            res = self.client.get(body["slices"][index]["image_url"])
            self.assertEqual(res.status_code, 200, index)
            self.assertEqual(res.content, self.h.png_by_path[self.h.paths[index]], index)
            self.assertEqual(res.headers["content-type"], "image/png")
            self.assertEqual(res.headers["cache-control"], "no-store")
            self.assertEqual(res.headers["content-disposition"], "inline")
        self.assertNotEqual(self.client.get(body["slices"][1]["image_url"]).content, self.client.get(body["slices"][2]["image_url"]).content)

    def test_image_doors_are_closed(self):
        self.login()
        sid = self.series().json()["series_id"]
        base = f"/learning/knee/series/{self.h.study_idx}/{sid}/image"
        self.client.cookies.clear()
        self.assertEqual(self.client.get(f"{base}/3").status_code, 401)
        self.login()
        for url, code in (
            (f"{base}/24", 404), (f"{base}/-1", 404), (f"{base}/03", 404), (f"{base}/1.5", 404), (f"{base}/abc", 404), (f"{base}/1e1", 404),
            (f"/learning/knee/series/{self.h.study_idx}/{'0' * 20}/image/3", 404), (f"/learning/knee/series/{self.h.study_idx}/{sid.upper()}/image/3", 404),
            (f"/learning/knee/series/{self.h.brain_study_idx}/{sid}/image/3", 404), (f"/learning/knee/series/99999/{sid}/image/3", 404),
            (f"/learning/knee/series/{self.h.unapproved_study_idx}/{sid}/image/3", 404),
        ):
            self.assertEqual(self.client.get(url).status_code, code, url)

    def test_changed_content_is_closed_and_other_slices_still_open(self):
        self.login()
        body = self.series().json()
        self.h.break_slice(7)
        broken = self.client.get(body["slices"][7]["image_url"])
        self.assertEqual(broken.status_code, 503)
        self.assertNotIn("corrupted", broken.text)
        self.assertNotIn(self.h.paths[7], broken.text)
        self.assertEqual(self.client.get(body["slices"][8]["image_url"]).status_code, 200)    # 한 장의 문제가 다른 장을 막지 않는다
        self.h.fix_slice(7)
        self.assertEqual(self.client.get(body["slices"][7]["image_url"]).status_code, 200)

    def test_wrong_size_registration_is_closed(self):
        self.login()
        series_assets.APPROVED_SERIES_ASSETS[self.h.study_idx] = [self.h.series_row(slice_sizes=tuple((320, 385) for _ in self.h.paths))]
        body = self.series().json()
        self.assertEqual(self.client.get(body["slices"][2]["image_url"]).status_code, 503)

    def test_registration_change_closes_old_series_address(self):
        self.login()
        old = self.series().json()
        series_assets.APPROVED_SERIES_ASSETS[self.h.study_idx] = [self.h.series_row(
            slice_sha256=tuple(hashlib.sha256(b"new%d" % i).hexdigest() for i in range(24)))]
        self.assertEqual(self.client.get(old["slices"][4]["image_url"]).status_code, 404)    # 옛 series_id 는 열리지 않는다
        series_assets.APPROVED_SERIES_ASSETS.pop(self.h.study_idx)
        self.assertEqual(self.client.get(old["slices"][4]["image_url"]).status_code, 404)

    def test_list_change_while_reading_closes_with_409(self):
        from routers import kneeLearning
        self.login()
        body = self.series().json()
        with mock.patch.object(kneeLearning, "_series_still_current", return_value=False):
            self.assertEqual(self.client.get(body["slices"][4]["image_url"]).status_code, 409)

    # ── task 연결과 채점(대표 1장 기준 유지) ──
    def test_task_carries_the_same_series_id(self):
        self.login()
        sid = self.series().json()["series_id"]
        task = self.task()
        self.assertEqual(task["series_id"], sid)
        self.assertEqual(task["representative_slice_index"], self.h.rep)

    def replace_slice(self, index, seed=99):
        """디스크의 한 장을 다른 합성 영상으로 바꾸고(끝나면 되돌림) 그 장의 새 SHA-256 을 돌려준다."""
        from knee_screen_harness import synthetic_png
        png = synthetic_png(seed)
        target = self.h.image_dir / self.h.paths[index].lstrip("/")
        original = target.read_bytes()
        target.write_bytes(png)
        self.addCleanup(target.write_bytes, original)
        return hashlib.sha256(png).hexdigest()

    def register_with(self, index, sha=None, size=None):
        """열람 등록부의 한 장 해시(와 치수)만 바꾼 행으로 교체한다."""
        row = self.h.series_row()
        hashes, sizes = list(row.slice_sha256), list(row.slice_sizes)
        if sha is not None:
            hashes[index] = sha
        if size is not None:
            sizes[index] = size
        series_assets.APPROVED_SERIES_ASSETS[self.h.study_idx] = [self.h.series_row(slice_sha256=tuple(hashes), slice_sizes=tuple(sizes), version="series-v2")]

    def test_series_id_follows_a_non_representative_slice_change_in_both_answers(self):
        """대표가 아닌 장의 내용이 바뀌면 열람 목록도 제시도 새 series_id 를 갖는다(대표 장·기준은 그대로라 채점은 계속 가능)."""
        self.login()
        first = self.series().json()["series_id"]
        self.register_with(3, sha=self.replace_slice(3))
        self.assertNotEqual(self.series().json()["series_id"], first)
        task = self.task()
        self.assertTrue(task["grading_available"])
        self.assertNotEqual(task["series_id"], first)
        self.assertEqual(task["series_id"], self.series().json()["series_id"])

    # ── FINDING-SERIES-002-01: 열람 영상(등록부)과 채점 기준(승인표)이 같은 대표 영상인지 교차 대조 ──
    def test_new_representative_bytes_with_old_reference_close_grading_but_keep_browsing(self):
        self.login()
        rep = self.h.rep
        self.register_with(rep, sha=self.replace_slice(rep))
        body = self.series().json()
        self.assertTrue(body["series_available"])
        shown = self.client.get(body["slices"][rep]["image_url"])
        self.assertEqual(shown.status_code, 200)     # 영상 탐색은 열려 있다(새 영상)
        self.assertNotEqual(shown.content, self.h.png_by_path[self.h.paths[rep]])
        res = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})
        self.assertEqual(res.status_code, 200)
        task = res.json()["knee_task"]
        self.assertFalse(task["grading_available"])
        self.assertEqual(task["unavailable_reason"], "series_reference_mismatch")
        self.assertIsNone(task["problem_token"])
        self.assertIsNone(task["series_id"])
        self.assertIsNone(task["image_url"])

    def test_representative_size_mismatch_closes_grading(self):
        self.login()
        self.register_with(self.h.rep, size=(320, 385))
        task = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx}).json()["knee_task"]
        self.assertEqual((task["grading_available"], task["unavailable_reason"]), (False, "series_reference_mismatch"))

    def test_registry_change_after_task_closes_submission_and_stores_nothing(self):
        self.login()
        task = self.task()                                        # 일치할 때 발급
        self.assertTrue(task["grading_available"])
        before = len(self.h.reviews())
        self.register_with(self.h.rep, sha=self.replace_slice(self.h.rep))   # 그 뒤 열람 영상이 새 것으로 바뀜
        data = self.grade(self.h.rep, task["problem_token"]).json()
        self.assertFalse(data["graded"])
        self.assertEqual(data["not_graded_reason"], "series_reference_mismatch")
        self.assertIsNone(data["result"])
        self.assertIsNone(data["review_idx"])
        self.assertEqual(len(self.h.reviews()), before)

    def test_old_token_cannot_be_used_after_the_pair_changes_and_is_closed_again_if_pair_breaks(self):
        self.login()
        old = self.task()
        # 등록·기준이 함께 새 영상으로 바뀌면(둘이 서로 맞음) 옛 토큰은 기준 번호·내용이 달라 쓸 수 없다
        new_sha = self.replace_slice(self.h.rep, seed=77)
        from dataclasses import replace
        self.register_with(self.h.rep, sha=new_sha)
        ref = self.h.ref
        slide_refs.APPROVED_SLIDE_REFERENCES[self.h.study_idx] = [replace(ref, image_sha256=new_sha, reference_id=ref.reference_id + 1, version="v2")]
        self.addCleanup(slide_refs.APPROVED_SLIDE_REFERENCES.__setitem__, self.h.study_idx, [ref])
        before = len(self.h.reviews())
        stale = self.grade(self.h.rep, old["problem_token"]).json()
        self.assertFalse(stale["graded"])
        self.assertEqual(len(self.h.reviews()), before)
        fresh = self.task()                                       # 새로 열면 새 짝(영상·기준이 일치)으로 채점된다
        self.assertTrue(fresh["grading_available"])
        self.assertTrue(self.grade(self.h.rep, fresh["problem_token"]).json()["graded"])

    def test_two_active_series_rows_close_both_browsing_and_grading(self):
        self.login()
        series_assets.APPROVED_SERIES_ASSETS[self.h.study_idx] = [self.h.series_row(), self.h.series_row(version="series-v9")]
        self.assertEqual(self.series().json()["unavailable_reason"], "series_ambiguous")
        task = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx}).json()["knee_task"]
        self.assertEqual((task["grading_available"], task["unavailable_reason"]), (False, "series_reference_mismatch"))

    def test_without_any_series_registration_the_single_image_mode_still_grades(self):
        """열람 시리즈 등록이 없는 문제(한 장 모드)는 교차 대조 때문에 막히지 않는다."""
        self.login()
        series_assets.APPROVED_SERIES_ASSETS.pop(self.h.study_idx)
        task = self.task()
        self.assertTrue(task["grading_available"])
        self.assertIsNone(task["series_id"])
        self.assertTrue(self.grade(self.h.rep, task["problem_token"]).json()["graded"])

    def test_matching_pair_still_grades_and_carries_series_id(self):
        self.login()
        task = self.task()
        self.assertEqual(task["series_id"], self.series().json()["series_id"])
        self.assertTrue(self.grade(self.h.rep, task["problem_token"]).json()["graded"])

    def test_only_the_representative_slice_is_graded_and_other_slices_are_not_graded_and_not_stored(self):
        self.login()
        task = self.task()
        before = len(self.h.reviews())
        other = self.grade(3, task["problem_token"])      # 대표가 아닌 유효한 장
        self.assertEqual(other.status_code, 200)
        data = other.json()
        self.assertFalse(data["graded"])
        self.assertEqual(data["not_graded_reason"], "no_reference_for_slice")
        self.assertIsNone(data["result"])
        self.assertIsNone(data["reference_bbox"])
        self.assertIsNone(data["review_idx"])
        self.assertEqual(len(self.h.reviews()), before)    # 저장 0건
        graded = self.grade(self.h.rep, task["problem_token"]).json()
        self.assertTrue(graded["graded"])
        self.assertEqual(graded["result"], "정답")
        self.assertEqual(len(self.h.reviews()), before + 1)

    def test_representative_box_is_never_copied_to_other_slices(self):
        self.login()
        task = self.task()
        for index in (0, self.h.rep - 1, self.h.rep + 1, 23):
            data = self.grade(index, task["problem_token"], box=(0.3, 0.3, 0.4, 0.4)).json()    # 기준 박스와 똑같이 그려도 다른 장은 채점하지 않는다
            self.assertFalse(data["graded"], index)
            self.assertNotIn("reference_bbox", {k for k, v in data.items() if v})

    def test_index_out_of_range_is_a_client_error_not_a_grade(self):
        self.login()
        task = self.task()
        self.assertEqual(self.grade(24, task["problem_token"]).status_code, 400)
        self.assertEqual(self.grade(-1, task["problem_token"]).status_code, 400)


if __name__ == "__main__":
    unittest.main()
