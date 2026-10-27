"""
무릎 데이터 묶음 선택(local / nas_presentation)과 영상 공급원 두 키의 명시적 빈 값 처리 시험(MSN-20261016-003 FIVE-05·06·08).

- FIVE-06: .env 에 로컬 폴더가 있어도 실행 환경변수 KNEE_IMAGE_DIR='' 이면 로컬 공급원이 꺼지고 NAS 만 실효된다. 라우터의 공급원 선택과
  local_assets/nas_assets 생성이 같은 값을 쓴다. 둘 다/없음은 닫힘. 다른 설정(토큰 등)의 get_setting 계약은 그대로.
- FIVE-05: local=기존 데이터 파일, nas_presentation=확정 JSON 묶음만. 잘못된 profile·묶음(후보·해시 불일치·대상 불일치·중복·필드 오류·파일 없음)은
  닫힘이며 local 로 대신하지 않는다.
- FIVE-08: 발표 묶음으로 올린 상태에서 무작위 후보는 묶음 케이스만, 대표 장 기준 박스 = 채점, 다른 장 = 미채점·저장 0, 다른 케이스 토큰 거절.
실행: python -m pytest tests/test_knee_data_profile.py
"""
import dataclasses
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

config = import_module("class.knee.config")
dp = import_module("class.knee.data_profile")
series_assets = import_module("class.knee.series_assets")
slide_refs = import_module("class.knee.slide_references")
local_assets = import_module("class.knee.local_assets")
nas_assets = import_module("class.knee.nas_assets")
kneeLearning = import_module("routers.kneeLearning")

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    import PIL  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False


class SourceSettingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.folder = self.tmp / "images"
        self.folder.mkdir()
        self.env_file = self.tmp / ".env"
        self.env_file.write_text(f"KNEE_IMAGE_DIR={self.folder}\nKNEE_PROBLEM_TOKEN_KEY=dotenv-token-key-value-0123456789abcdef\n", encoding="utf-8")
        self.patches = [mock.patch.object(config, "ENV_PATH", self.env_file)]
        for p in self.patches:
            p.start()
        self.clean = {k: v for k, v in os.environ.items() if k not in ("KNEE_IMAGE_DIR", "KNEE_NAS_IMAGE_BASE_URL", "KNEE_PROBLEM_TOKEN_KEY")}

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_key_absent_reads_dotenv_and_empty_env_disables(self):
        with mock.patch.dict(os.environ, self.clean, clear=True):
            self.assertEqual(config.source_setting("KNEE_IMAGE_DIR"), str(self.folder))
            self.assertEqual(config.get_setting("KNEE_IMAGE_DIR"), str(self.folder))
        with mock.patch.dict(os.environ, dict(self.clean, KNEE_IMAGE_DIR=""), clear=True):
            self.assertEqual(config.source_setting("KNEE_IMAGE_DIR"), "")                 # 명시적 비활성
            self.assertEqual(config.get_setting("KNEE_IMAGE_DIR"), str(self.folder))     # 일반 설정 계약은 그대로(.env 로 채움)
            self.assertIsNone(local_assets.source_from_settings())

    def test_only_source_keys_allowed_and_token_contract_unchanged(self):
        with self.assertRaises(ValueError):
            config.source_setting("KNEE_PROBLEM_TOKEN_KEY")
        with mock.patch.dict(os.environ, dict(self.clean, KNEE_PROBLEM_TOKEN_KEY=""), clear=True):
            self.assertEqual(config.get_setting("KNEE_PROBLEM_TOKEN_KEY"), "dotenv-token-key-value-0123456789abcdef")

    def test_router_selection_matches_source_creation(self):
        nas_env = dict(self.clean, KNEE_NAS_IMAGE_BASE_URL="https://nas.example.test/mu_shop/public")
        with mock.patch.dict(os.environ, nas_env, clear=True):
            self.assertIsNone(kneeLearning._image_source())                               # .env 로컬 + 환경 NAS = 둘 다 → 닫힘
        with mock.patch.dict(os.environ, dict(nas_env, KNEE_IMAGE_DIR=""), clear=True):
            src = kneeLearning._image_source()
            self.assertIsInstance(src, nas_assets.NasImageSource)                          # 로컬을 명시적으로 끄면 NAS 만
            self.assertIsNone(local_assets.source_from_settings())
        with mock.patch.dict(os.environ, self.clean, clear=True):
            self.assertIsInstance(kneeLearning._image_source(), local_assets.LocalImageSource)   # 기존 로컬 구성 그대로
        with mock.patch.dict(os.environ, dict(self.clean, KNEE_IMAGE_DIR="", KNEE_NAS_IMAGE_BASE_URL=""), clear=True):
            self.assertIsNone(kneeLearning._image_source())                               # 둘 다 없음 → 닫힘
        with mock.patch.dict(os.environ, dict(self.clean, KNEE_IMAGE_DIR="", KNEE_NAS_IMAGE_BASE_URL="http://nas.example.test"), clear=True):
            self.assertIsNone(kneeLearning._image_source())                               # https 아님 → 닫힘(로컬로 대신하지 않음)


def series_row(idx, n=3):
    paths = [f"/stylesheets/assets/Knee/MRI/{'a' * 10}_{i + 1:02d}_case-{idx}.png" for i in range(n)]
    man = import_module("class.knee.slice_manifest").build_manifest(json.dumps(paths), ("/stylesheets/assets/Knee/MRI/",))
    return {"study_idx": idx, "manifest_version": man.version, "slice_set_sha256": man.list_sha256, "slice_sha256": [f"{i:064x}" for i in range(n)],
            "slice_sizes": [[320, 320]] * n, "kind": "real", "notice": "n", "version": "series-nas-v1", "registered_by": "registrar-001",
            "registered_at": "2026-10-19T00:00:00", "paths": paths}, man, paths


def ref_row(idx, rid, man, paths):
    return {"reference_id": rid, "study_idx": idx, "manifest_version": man.version, "slice_set_sha256": man.list_sha256, "slice_no": 1,
            "image_path": paths[1], "image_sha256": f"{1:064x}", "plane": "sagittal", "x": 0.3, "y": 0.3, "width": 0.2, "height": 0.2,
            "source": "s", "version": "v1-nas", "reviewed_by": "reviewer-001", "reviewed_at": "2026-10-19T00:00:00",
            "source_slice_no": 1, "selection_reason": "", "image_width": 320, "image_height": 320}


def bundle(targets=(9001, 9002), status="confirmed", refs=None, tamper=False):
    reg, ref = [], []
    for n, idx in enumerate(targets):
        r, man, paths = series_row(idx)
        reg.append(r)
        if refs is None or idx in refs:
            ref.append(ref_row(idx, 2001 + n, man, paths))
    body = {"schema_version": dp.SCHEMA_VERSION, "status": status, "bundle_version": "t", "target_study_idx": list(targets),
            "registered_rows": reg, "reference_rows": ref, "meta": {}, "content_sha256": dp.content_sha256(list(targets), reg, ref)}
    if tamper:
        body["reference_rows"][0]["x"] = 0.31
    return body


class DataProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        dp.reset_for_tests()
        self.addCleanup(dp.reset_for_tests)

    def write(self, body):
        p = self.tmp / "bundle.json"
        p.write_text(json.dumps(body), encoding="utf-8")
        return str(p)

    def state(self, profile, path=""):
        dp.reset_for_tests()
        with mock.patch.dict(os.environ, {"KNEE_DATA_PROFILE": profile, "KNEE_PRESENTATION_BUNDLE": path}):
            return dp.state()

    def load_tables(self, profile, path=""):
        """서버 재시작을 흉내: 빈 표에 로더를 다시 돌린 결과."""
        dp.reset_for_tests()
        with mock.patch.dict(os.environ, {"KNEE_DATA_PROFILE": profile, "KNEE_PRESENTATION_BUNDLE": path}), \
                mock.patch.dict(series_assets.APPROVED_SERIES_ASSETS, {}, clear=True), mock.patch.dict(slide_refs.APPROVED_SLIDE_REFERENCES, {}, clear=True):
            series_assets._load_registered_rows()
            slide_refs._load_reference_rows()
            return sorted(series_assets.APPROVED_SERIES_ASSETS), sorted(slide_refs.APPROVED_SLIDE_REFERENCES)

    def test_field_sets_match_dataclasses(self):
        self.assertEqual(dp.SERIES_REQUIRED | (dp.SERIES_OPTIONAL - {"paths"}), {f.name for f in dataclasses.fields(series_assets.SeriesAssets)})
        self.assertEqual(dp.REFERENCE_REQUIRED | dp.REFERENCE_OPTIONAL, {f.name for f in dataclasses.fields(slide_refs.SlideReference)})

    def test_local_uses_existing_data_files(self):
        self.assertIsNone(self.state("local")["registered"])
        reg, ref = self.load_tables("local")
        data = import_module("class.knee.series_registered_data").REGISTERED_ROWS
        self.assertEqual(reg, sorted({r["study_idx"] for r in data}))
        self.assertEqual(len(ref), len(import_module("class.knee.slide_references_data").REFERENCE_ROWS))

    def test_nas_presentation_loads_only_bundle(self):
        path = self.write(bundle())
        reg, ref = self.load_tables("nas_presentation", path)
        self.assertEqual((reg, ref), ([9001, 9002], [9001, 9002]))   # 기존 908·기준 10 은 섞이지 않는다

    def test_bad_profiles_and_bundles_close_without_local_fallback(self):
        cases = {
            "unknown profile": ("presentation", self.write(bundle())),
            "missing path": ("nas_presentation", ""),
            "missing file": ("nas_presentation", str(self.tmp / "nope.json")),
            "candidate": ("nas_presentation", self.write(bundle(status="candidate"))),
            "tampered": ("nas_presentation", self.write(bundle(tamper=True))),
        }
        b = bundle()
        b["reference_rows"].append(dict(b["reference_rows"][0], reference_id=2999))   # 같은 study 승인 둘
        b["content_sha256"] = dp.content_sha256(b["target_study_idx"], b["registered_rows"], b["reference_rows"])
        cases["duplicate study approval"] = ("nas_presentation", self.write(b))
        b2 = bundle()
        b2["target_study_idx"] = [9001, 9002, 9003]
        b2["content_sha256"] = dp.content_sha256(b2["target_study_idx"], b2["registered_rows"], b2["reference_rows"])
        cases["target mismatch"] = ("nas_presentation", self.write(b2))
        b3 = bundle()
        b3["registered_rows"][0]["surprise"] = 1
        b3["content_sha256"] = dp.content_sha256(b3["target_study_idx"], b3["registered_rows"], b3["reference_rows"])
        cases["unknown field"] = ("nas_presentation", self.write(b3))
        bad_json = self.tmp / "bad.json"
        bad_json.write_text("{not json", encoding="utf-8")
        cases["broken json"] = ("nas_presentation", str(bad_json))
        for name, (profile, path) in cases.items():
            st = self.state(profile, path)
            self.assertFalse(st["ok"], name)
            self.assertEqual(self.load_tables(profile, path), ([], []), name)   # local 로 대신하지 않음

    def test_partial_references_allowed_but_not_extra(self):
        path = self.write(bundle(refs={9001}))
        self.assertEqual(self.load_tables("nas_presentation", path), ([9001, 9002], [9001]))

    def test_state_is_fixed_for_process_lifetime(self):
        path = self.write(bundle())
        dp.reset_for_tests()
        with mock.patch.dict(os.environ, {"KNEE_DATA_PROFILE": "nas_presentation", "KNEE_PRESENTATION_BUNDLE": path}):
            first = dp.state()
        with mock.patch.dict(os.environ, {"KNEE_DATA_PROFILE": "local"}):
            self.assertIs(dp.state(), first)   # 재시작 전에는 바뀌지 않는다


@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg/pillow 가 없어 임시 DB 시험을 건너뜀")
class PresentationBundleApiTests(unittest.TestCase):
    """FIVE-08: 합성 하네스 케이스로 확정 묶음을 만들어 로더로 올린 뒤(재시작 흉내) 실제 라우터로 확인한다(임시 DB, 저장은 임시 DB 에만)."""

    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        cls.h = Harness(series=True, extra_cases=(18, 31))

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    def setUp(self):
        self.client = TestClient(self.h.app, raise_server_exceptions=False)
        case1, case2 = self.h.extra
        reg = []
        for case in (case1, case2):
            row = dataclasses.asdict(self.h.extra_series_row(case))
            row["slice_sha256"], row["slice_sizes"] = list(row["slice_sha256"]), [list(s) for s in row["slice_sizes"]]
            reg.append(row)
        ref = [dataclasses.asdict(case1["ref"])]
        targets = [case1["study_idx"], case2["study_idx"]]
        body = {"schema_version": dp.SCHEMA_VERSION, "status": "confirmed", "bundle_version": "t", "target_study_idx": targets,
                "registered_rows": reg, "reference_rows": ref, "meta": {}, "content_sha256": dp.content_sha256(targets, reg, ref)}
        self.path = Path(tempfile.mkdtemp()) / "bundle.json"
        self.path.write_text(json.dumps(body), encoding="utf-8")
        self.case1, self.case2 = case1, case2
        dp.reset_for_tests()
        self.addCleanup(dp.reset_for_tests)
        env = mock.patch.dict(os.environ, {"KNEE_DATA_PROFILE": "nas_presentation", "KNEE_PRESENTATION_BUNDLE": str(self.path)})
        env.start()
        self.addCleanup(env.stop)
        for table in (series_assets.APPROVED_SERIES_ASSETS, slide_refs.APPROVED_SLIDE_REFERENCES):
            p = mock.patch.dict(table, {}, clear=True)
            p.start()
            self.addCleanup(p.stop)
        series_assets._load_registered_rows()
        slide_refs._load_reference_rows()
        self.assertEqual(self.client.post("/_login", params={"idx": self.h.user_idx}).status_code, 200)

    def grade(self, idx, sl, token, box):
        return self.client.post("/learning/roi-grade/knee", data={"study_idx": str(idx), "roi_type": "box", "x": str(box[0]), "y": str(box[1]),
                                                                 "width": str(box[2]), "height": str(box[3]), "slice_index": str(sl), "problem_token": token or ""}).json()

    def test_random_pool_is_bundle_only(self):
        picks = {self.client.get("/learning/knee/random").json()["study_idx"] for _ in range(40)}
        self.assertEqual(picks, {self.case1["study_idx"], self.case2["study_idx"]})
        self.assertNotIn(self.h.study_idx, picks)                                       # local(묶음 밖) 케이스 0
        self.assertEqual(self.client.get("/learning/knee/series", params={"study_idx": self.h.study_idx}).json()["series_available"], False)

    def test_grading_only_on_bundle_reference_slice(self):
        ref = self.case1["ref"]
        before = len(self.h.reviews())
        task = self.client.get("/learning/knee/task", params={"study_idx": self.case1["study_idx"]}).json()["knee_task"]
        self.assertTrue(task["grading_available"])
        box = (ref.x, ref.y, ref.width, ref.height)
        ok = self.grade(self.case1["study_idx"], ref.slice_no, task["problem_token"], box)
        self.assertEqual((ok["graded"], ok["result"]), (True, "정답"))
        other = self.grade(self.case1["study_idx"], ref.slice_no + 1, task["problem_token"], box)
        self.assertEqual((other["graded"], other.get("review_idx")), (False, None))
        self.assertEqual(len(self.h.reviews()), before + 1)                              # 채점된 1건만 임시 DB 에 저장
        none_task = self.client.get("/learning/knee/task", params={"study_idx": self.case2["study_idx"]}).json()["knee_task"]
        self.assertFalse(none_task["grading_available"])                                 # 기준 없는 묶음 케이스는 채점 안 함
        crossed = self.grade(self.case2["study_idx"], ref.slice_no, task["problem_token"], box)
        self.assertFalse(crossed["graded"])                                              # 다른 케이스 토큰 거절


if __name__ == "__main__":
    unittest.main()
