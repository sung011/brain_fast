"""
서버 폴더 영상 공급(class/knee/local_assets.py)과 "제출 때도 영상 검증"(asset_verifier 연결) 시험.

- 폴더 공급원의 안전 규칙(폴더 밖·링크·형식·크기·한 번 읽은 바이트 검증)은 임시 폴더의 합성 PNG 로 확인한다.
- 문제 제시·영상 주소·제출은 임시 PostgreSQL 위에서 실제 라우터로 확인한다(NAS 공급원의 제출 검증·승인 치수 포함).
실제 영상·실제 NAS·실제 DB 는 쓰지 않는다. 실행: python -m pytest tests/test_knee_local_assets.py
"""
import dataclasses
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import time
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
knee_config = import_module("class.knee.config")
slide_refs = import_module("class.knee.slide_references")
ROOT = Path(__file__).resolve().parents[1]

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    import PIL  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False


def png(width=8, height=6, seed=0) -> bytes:
    from PIL import Image
    image = Image.new("L", (width, height), (seed * 37) % 256)
    image.putpixel((0, 0), 255 - (seed % 200))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FolderCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="knee_local_assets_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "study").mkdir()
        self.data = png(8, 6, seed=3)
        (self.root / "study" / "a.png").write_bytes(self.data)
        self.source = local.LocalImageSource(self.root)


class LocalSourceTests(FolderCase):
    def test_reads_verifies_and_returns_the_hashed_bytes(self):
        image = self.source.get("/study/a.png", sha(self.data))
        self.assertEqual((image.width, image.height, image.sha256, image.data), (8, 6, sha(self.data), self.data))

    def test_served_bytes_are_the_very_bytes_that_were_hashed(self):
        """한 번 읽은 바이트를 검사하고 그대로 낸다(검사 후 다시 열어 보내지 않는다)."""
        seen = []
        real = hashlib.sha256

        def spy(data=b"", *a, **k):
            seen.append(data)
            return real(data, *a, **k)
        with mock.patch.object(local.hashlib, "sha256", spy):
            image = self.source.get("/study/a.png", sha(self.data))
        self.assertTrue(any(item is image.data for item in seen))

    def test_the_file_is_opened_exactly_once(self):
        opened = []
        real_open = open

        def counting_open(path, *a, **k):
            if str(path).endswith("a.png"):
                opened.append(str(path))
            return real_open(path, *a, **k)
        with mock.patch("builtins.open", counting_open):
            self.source.get("/study/a.png", sha(self.data))
        self.assertEqual(len(opened), 1)

    def test_a_replaced_file_is_refused_and_nothing_is_cached(self):
        self.source.get("/study/a.png", sha(self.data))
        (self.root / "study" / "a.png").write_bytes(png(8, 6, seed=9))        # 같은 경로에 다른 내용(덮어쓰기)
        with self.assertRaises(local.AssetMismatch):
            self.source.get("/study/a.png", sha(self.data))
        (self.root / "study" / "a.png").write_bytes(self.data)                # 원래 내용으로 돌아오면 다시 통과
        self.assertEqual(self.source.get("/study/a.png", sha(self.data)).data, self.data)

    def test_bad_files_are_unavailable(self):
        (self.root / "study" / "text.png").write_bytes(b"not an image at all" * 4)
        (self.root / "empty.png").write_bytes(b"")
        (self.root / "study" / "dir.png").mkdir()
        cases = {
            "/study/missing.png": "0" * 64,
            "/study/text.png": sha(b"not an image at all" * 4),        # 해시가 맞아도 PNG 가 아니다
            "/empty.png": sha(b""),
            "/study/dir.png": "0" * 64,                                 # 폴더
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                with self.assertRaises(local.AssetUnavailable):
                    self.source.get(path, expected)

    def test_oversized_file_is_refused_before_hashing(self):
        big = self.root / "big.png"
        with open(big, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
            handle.truncate(local.MAX_BYTES + 5)
        with self.assertRaises(local.AssetUnavailable):
            self.source.get("/big.png", "0" * 64)

    def test_unsafe_paths_never_reach_the_file_system(self):
        outside = self.root.parent / "outside_secret.png"
        outside.write_bytes(self.data)
        self.addCleanup(outside.unlink)
        for bad in ("../outside_secret.png", "/../outside_secret.png", "/study/../../outside_secret.png", "/study/./a.png", "study/a.png", "//study/a.png",
                    "\\study\\a.png", "/study\\a.png", "C:/Windows/win.ini", "/C:/Windows/win.ini", "/study/a.png:stream", "/study/a.png?x", "/study/a%2e.png",
                    "/study/a\x00.png", "", None, 7):
            with self.subTest(path=bad):
                with self.assertRaises(local.AssetUnavailable):
                    self.source.get(bad, sha(self.data))

    def test_bad_expected_hash_is_unavailable(self):
        for bad in ("", "abc", "G" * 64, "A" * 64, None):
            with self.assertRaises(local.AssetUnavailable):
                self.source.get("/study/a.png", bad)

    def test_symlinked_file_or_folder_is_refused(self):
        target_dir = self.root.parent / "linked_target_dir"
        target_dir.mkdir(exist_ok=True)
        (target_dir / "b.png").write_bytes(self.data)
        self.addCleanup(lambda: [p.unlink() for p in target_dir.glob("*")] and target_dir.rmdir())
        made = []
        try:
            os.symlink(self.root / "study" / "a.png", self.root / "link_file.png")
            made.append("/link_file.png")
        except (OSError, NotImplementedError):
            pass
        try:
            os.symlink(target_dir, self.root / "link_dir", target_is_directory=True)
            made.append("/link_dir/b.png")
        except (OSError, NotImplementedError):
            pass
        if os.name == "nt":                                        # 정션은 관리자 권한 없이 만들 수 있다
            done = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.root / "junction_dir"), str(target_dir)], capture_output=True)
            if done.returncode == 0:
                made.append("/junction_dir/b.png")
        if not made:
            self.skipTest("이 환경에서는 링크를 만들 수 없다")
        for path in made:
            with self.subTest(path=path):
                with self.assertRaises(local.AssetUnavailable):
                    self.source.get(path, sha(self.data))
        if os.name == "nt" and (self.root / "junction_dir").exists():
            os.rmdir(self.root / "junction_dir")                  # 정션은 폴더처럼 지운다(대상 내용은 지우지 않는다)

    def test_link_inside_the_root_is_refused_too(self):
        """링크가 폴더 **안**을 가리켜도 거절한다(폴더 밖 검사와 별개로 링크 자체를 막는다)."""
        (self.root / "real_dir").mkdir()
        (self.root / "real_dir" / "c.png").write_bytes(self.data)
        made = False
        try:
            os.symlink(self.root / "real_dir", self.root / "inner_link", target_is_directory=True)
            made = True
        except (OSError, NotImplementedError):
            pass
        if not made and os.name == "nt":
            made = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.root / "inner_link"), str(self.root / "real_dir")], capture_output=True).returncode == 0
        if not made:
            self.skipTest("이 환경에서는 링크를 만들 수 없다")
        self.assertEqual(self.source.get("/real_dir/c.png", sha(self.data)).data, self.data)   # 링크 없이 직접 가면 된다
        with self.assertRaises(local.AssetUnavailable):
            self.source.get("/inner_link/c.png", sha(self.data))
        os.rmdir(self.root / "inner_link") if (self.root / "inner_link").is_dir() and not (self.root / "inner_link").is_symlink() else None

    def test_root_that_contains_a_public_folder_is_refused(self):
        """Codex 20차 ASSET-020-04: 공개 폴더의 상위 폴더를 root 로 잡으면 공개 하위 파일이 인증 경로로도 읽힌다 → 설정 자체를 거절."""
        for parent in (ROOT, ROOT.parent):
            with self.subTest(root=str(parent)):
                with self.assertRaises(local.AssetUnavailable):
                    local.LocalImageSource(parent)

    def test_overlap_with_a_public_folder_is_refused_for_any_public_root(self):
        """root 가 공개 폴더의 상위(합성 재현: tmp 가 공개 폴더 tmp/public 을 포함)이면 거절하고, 포함하지 않으면 허용한다."""
        public = self.root / "public"
        public.mkdir()
        (public / "image.png").write_bytes(self.data)
        with mock.patch.object(local, "_FORBIDDEN_ROOTS", (public,)):
            with self.assertRaises(local.AssetUnavailable):
                local.LocalImageSource(self.root)                       # 상위 root
            with self.assertRaises(local.AssetUnavailable):
                local.LocalImageSource(public)                          # 공개 폴더 자체
            with self.assertRaises(local.AssetUnavailable):
                local.LocalImageSource(public / "..")                   # 같은 실제 경로(상위)를 다른 표기로
            self.assertIsNotNone(local.LocalImageSource(self.root / "study"))   # 공개 폴더와 겹치지 않는 폴더는 허용

    def test_target_inside_a_public_folder_is_refused_even_if_the_root_slipped_through(self):
        """root 검사를 통과한 뒤에 공개 폴더가 생겼다고 가정해도, 실제 접근 대상이 공개 폴더 안이면 거절한다(한 번 더 방어)."""
        public = self.root / "public"
        public.mkdir()
        (public / "image.png").write_bytes(self.data)
        source = local.LocalImageSource(self.root)                        # 공개 폴더 지정 전에는 허용된 root
        self.assertEqual(source.get("/public/image.png", sha(self.data)).data, self.data)
        with mock.patch.object(local, "_FORBIDDEN_ROOTS", (public,)):
            with self.assertRaises(local.AssetUnavailable):
                source.get("/public/image.png", sha(self.data))         # 동일 파일이 정적 공개 폴더 안
            self.assertEqual(source.get("/study/a.png", sha(self.data)).data, self.data)   # 공개 폴더 밖의 파일은 계속 된다

    def test_root_rules(self):
        with self.assertRaises(local.AssetUnavailable):
            local.LocalImageSource("relative/folder")
        with self.assertRaises(local.AssetUnavailable):
            local.LocalImageSource(self.root / "missing")
        with self.assertRaises(local.AssetUnavailable):
            local.LocalImageSource(self.root / "study" / "a.png")  # 파일은 폴더가 아니다
        for public in (ROOT / "assets", ROOT / "assets" / "css", ROOT / "templates"):
            with self.subTest(root=str(public)):
                with self.assertRaises(local.AssetUnavailable):
                    local.LocalImageSource(public)                 # 정적 공개 폴더 안은 쓰지 않는다


class LocalSettingsTests(FolderCase):
    def setUp(self):
        super().setUp()
        dotenv = mock.patch.object(knee_config, "ENV_PATH", Path(os.devnull) / "없음.env")
        dotenv.start()
        self.addCleanup(dotenv.stop)

    def test_closed_unless_configured_with_a_valid_absolute_folder(self):
        for value in ("", "relative", str(self.root / "missing"), str(ROOT / "assets")):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": value}):
                    self.assertIsNone(local.source_from_settings())
        with mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": str(self.root)}):
            self.assertIsNotNone(local.source_from_settings())


# ── 문제 제시·영상 주소·제출 (임시 DB) ──────────────────────────────────────────────
@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg/pillow 가 없어 임시 DB 시험을 건너뜀")
class FolderConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        cls.h = Harness()

    @classmethod
    def tearDownClass(cls):
        cls.h.close()

    def setUp(self):
        self.client = TestClient(self.h.app, raise_server_exceptions=False)
        self.h.clock.offset = 0
        self.tmp = tempfile.TemporaryDirectory(prefix="knee_local_conn_")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()
        for path, data in self.h.png_by_path.items():
            target = self.folder / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        kneeLearning.set_asset_provider(None)
        self.addCleanup(kneeLearning.set_asset_provider,
                        lambda ref, manifest: {"image_url": f"/_images/rep-{ref.reference_id}.png", "image_width": 320, "image_height": 384})
        env = mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": str(self.folder), "KNEE_NAS_IMAGE_BASE_URL": "", "KNEE_NAS_ALLOW_HTTP": ""})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.h.swap_reference, 12, reference_id=7, version="v1")
        self.assertEqual(self.client.post("/_login", params={"idx": self.h.user_idx}).status_code, 200)

    def file(self, index=12) -> Path:
        return self.folder / self.h.paths[index].lstrip("/")

    def task(self, expect=200):
        res = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})
        self.assertEqual(res.status_code, expect, res.text)
        return res

    def grade(self, task):
        return self.client.post("/learning/roi-grade/knee", data={
            "study_idx": str(self.h.study_idx), "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4",
            "slice_index": str(task["representative_slice_index"]), "problem_token": task["problem_token"]})

    def test_present_serve_and_grade_from_the_folder(self):
        task = self.task().json()["knee_task"]
        self.assertRegex(task["image_url"], rf"^/learning/knee/image/{self.h.study_idx}/[0-9a-f]{{16}}$")
        for secret in (str(self.folder), self.h.paths[12], self.h.rep_sha, "a.png"):
            self.assertNotIn(secret, str(task))
        res = self.client.get(task["image_url"])
        self.assertEqual((res.status_code, res.content), (200, self.h.rep_png))
        self.assertEqual(res.headers["cache-control"], "no-store")
        before = len(self.h.reviews())
        graded = self.grade(task).json()
        self.assertTrue(graded["graded"])
        self.assertEqual(len(self.h.reviews()), before + 1)

    def test_content_changed_after_presenting_is_not_graded_and_not_saved(self):
        task = self.task().json()["knee_task"]
        self.file().write_bytes(png(320, 384, seed=77))              # 제시 후 같은 경로에 다른 내용
        before = len(self.h.reviews())
        res = self.grade(task).json()
        self.assertEqual((res["graded"], res["not_graded_reason"], res["review_idx"]), (False, "asset_changed", None))
        self.assertEqual(len(self.h.reviews()), before)
        self.assertEqual(self.client.get(task["image_url"]).status_code, 503)   # 영상 주소도 바뀐 내용을 내보내지 않는다

    def test_file_removed_after_presenting_is_unavailable(self):
        task = self.task().json()["knee_task"]
        self.file().unlink()
        before = len(self.h.reviews())
        res = self.grade(task).json()
        self.assertEqual((res["graded"], res["not_graded_reason"]), (False, "study_image_unavailable"))
        self.assertEqual(len(self.h.reviews()), before)

    def test_content_changed_before_presenting_closes_the_task(self):
        self.file().write_bytes(png(320, 384, seed=78))
        res = self.task(503)
        self.assertNotIn("problem_token", res.text)
        self.assertNotIn(str(self.folder), res.text)

    def test_png_size_must_match_the_approved_size(self):
        approved = self.h.ref
        wrong = dataclasses.replace(approved, image_width=999)
        with mock.patch.dict(slide_refs.APPROVED_SLIDE_REFERENCES, {self.h.study_idx: [wrong]}):
            res = self.task(503)                                     # 제시하지 않는다
            self.assertNotIn("problem_token", res.text)
            self.assertEqual(self.client.get(f"/learning/knee/image/{self.h.study_idx}/{self._pid(wrong)}").status_code, 503)
        task = self.task().json()["knee_task"]                       # 승인 치수가 맞을 때 제시한 토큰으로
        with mock.patch.dict(slide_refs.APPROVED_SLIDE_REFERENCES, {self.h.study_idx: [wrong]}):
            res = self.grade(task).json()                            # 제출 때 승인 치수가 달라졌다면 닫힌다
        self.assertEqual((res["graded"], res["not_graded_reason"]), (False, "asset_changed"))

    def test_approval_without_a_size_cannot_use_a_source(self):
        unsized = dataclasses.replace(self.h.ref, image_width=None, image_height=None)
        with mock.patch.dict(slide_refs.APPROVED_SLIDE_REFERENCES, {self.h.study_idx: [unsized]}):
            self.task(503)

    def test_partial_or_invalid_size_makes_the_approval_stale(self):
        for width, height in ((320, None), (None, 384), (0, 384), (-1, 384), (True, 384), (320.5, 384), ("320", 384)):
            with self.subTest(size=(width, height)):
                bad = dataclasses.replace(self.h.ref, image_width=width, image_height=height)
                resolution = slide_refs.resolve(self.h.study_idx, self.h.manifest) if False else None
                with mock.patch.dict(slide_refs.APPROVED_SLIDE_REFERENCES, {self.h.study_idx: [bad]}):
                    self.assertEqual(slide_refs.resolve(self.h.study_idx, self.h.manifest).status, "none")

    def test_source_selection_is_closed_when_both_or_neither_is_configured(self):
        self.assertIsNotNone(kneeLearning._image_source())                      # 폴더만
        with mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": "https://nas.example.test/p"}):
            self.assertIsNone(kneeLearning._image_source())                     # 둘 다 → 모호하므로 닫힘
            self.assertIsNone(kneeLearning._default_provider())
            self.assertIsNone(kneeLearning._default_verifier())
        with mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": ""}):
            self.assertIsNone(kneeLearning._image_source())                     # 아무것도 없음
        with mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": "", "KNEE_NAS_IMAGE_BASE_URL": "https://nas.example.test/p"}):
            self.assertIsNotNone(kneeLearning._image_source())                  # NAS 만

    def test_two_sources_or_none_means_closed(self):
        with mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": "https://nas.example.test/p"}):
            self.task(503)                                           # 둘 다 설정하면 모호하므로 닫는다
        with mock.patch.dict(os.environ, {"KNEE_IMAGE_DIR": ""}):
            self.task(503)

    def test_replaced_between_checks_never_serves_unverified_bytes(self):
        """읽는 순간의 바이트만 검증·제공: 파일을 계속 바꿔도 해시가 맞는 바이트가 아니면 절대 나가지 않는다."""
        task = self.task().json()["knee_task"]
        good, evil = self.h.rep_png, png(320, 384, seed=79)
        for index in range(6):
            self.file().write_bytes(evil if index % 2 == 0 else good)
            res = self.client.get(task["image_url"])
            self.assertIn(res.status_code, (200, 503))
            if res.status_code == 200:
                self.assertEqual(res.content, good)

    def test_image_address_needs_the_login_and_the_current_approval(self):
        task = self.task().json()["knee_task"]
        anonymous = TestClient(self.h.app, raise_server_exceptions=False)
        self.assertEqual(anonymous.get(task["image_url"]).status_code, 401)
        self.h.swap_reference(13, reference_id=8, version="v2")
        self.assertEqual(self.client.get(task["image_url"]).status_code, 404)

    def _pid(self, ref):
        from services import kneeSliceGradeServices as service
        return service.presentation_id(knee_config.problem_token_key(), study_idx=self.h.study_idx, manifest=self.h.manifest, reference=ref)


# ── NAS 공급원의 제출 검증 ──────────────────────────────────────────────────────────
@unittest.skipUnless(HAVE_DB_TOOLS, "임시 DB 도구 없음")
class NasSubmitVerificationTests(unittest.TestCase):
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
        kneeLearning.set_asset_provider(None)
        self.addCleanup(kneeLearning.set_asset_provider,
                        lambda ref, manifest: {"image_url": f"/_images/rep-{ref.reference_id}.png", "image_width": 320, "image_height": 384})
        env = mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": self.nas.base, "KNEE_NAS_ALLOW_HTTP": "1", "KNEE_IMAGE_DIR": ""})
        env.start()
        self.addCleanup(env.stop)
        self.nas.routes.clear()
        for path, data in self.h.png_by_path.items():
            self.nas.serve_png(path, data)
        self.source = nas.source_from_settings()
        self.source.clear_cache()
        self.source._clock = time.monotonic                              # 공급원은 시험 사이에 공유되므로 시계를 되돌려 놓는다
        self.addCleanup(setattr, self.source, "_clock", time.monotonic)
        self.assertEqual(self.client.post("/_login", params={"idx": self.h.user_idx}).status_code, 200)

    def task(self):
        res = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["knee_task"]

    def grade(self, task):
        return self.client.post("/learning/roi-grade/knee", data={
            "study_idx": str(self.h.study_idx), "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4",
            "slice_index": str(task["representative_slice_index"]), "problem_token": task["problem_token"]}).json()

    def test_normal_submit_is_graded_after_verification(self):
        task = self.task()
        self.assertTrue(self.grade(task)["graded"])

    def test_nas_content_replaced_after_the_cache_expires_is_not_graded(self):
        task = self.task()
        self.nas.serve_png(self.h.paths[12], png(320, 384, seed=99))   # NAS 파일이 바뀌었다
        self.source._clock = lambda: 10 ** 9                            # 검증 결과의 보관 시간이 지났다(시험용 시계)
        before = len(self.h.reviews())
        res = self.grade(task)
        self.assertEqual((res["graded"], res["not_graded_reason"], res["review_idx"]), (False, "asset_changed", None))
        self.assertEqual(len(self.h.reviews()), before)

    def test_nas_down_at_submit_is_unavailable_after_the_cache_expires(self):
        task = self.task()
        self.nas.routes.clear()
        self.source._clock = lambda: 10 ** 9
        before = len(self.h.reviews())
        res = self.grade(task)
        self.assertEqual((res["graded"], res["not_graded_reason"]), (False, "study_image_unavailable"))
        self.assertEqual(len(self.h.reviews()), before)

    def test_recently_verified_image_is_reused_at_submit(self):
        task = self.task()
        self.nas.routes.clear()                                         # 방금 검증했으므로 NAS 가 잠깐 끊겨도 제출은 된다
        self.assertTrue(self.grade(task)["graded"])


if __name__ == "__main__":
    unittest.main()
