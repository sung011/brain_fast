"""
NAS 영상 공급(class/knee/nas_assets.py)과 영상 주소(GET /learning/knee/image/...) 시험.

실제 NAS 는 쓰지 않는다. 로컬에서 임시로 띄운 **가짜 NAS 서버**(127.0.0.1)와 합성 PNG 만 쓰고, 끝나면 모두 지운다.
- 안전 규칙(주소 검증·리다이렉트 거부·시간/크기 제한·해시 대조)은 실제 코드와 실제 로컬 HTTP 서버로 확인한다.
- 문제 제시·영상 주소·로그인은 임시 PostgreSQL 위에서 실제 라우터로 확인한다.
실행: python -m pytest tests/test_knee_nas_assets.py   (DB 시험은 pgserver, psycopg, pillow 필요. 없으면 건너뜀)
"""
import hashlib
import http.server
import os
import re
import sys
import threading
import time
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

from routers import kneeLearning

nas = import_module("class.knee.nas_assets")
knee_config = import_module("class.knee.config")

try:
    import pgserver  # noqa: F401
    import psycopg  # noqa: F401
    import PIL  # noqa: F401
    HAVE_DB_TOOLS = True
except ImportError:  # pragma: no cover
    HAVE_DB_TOOLS = False


def png(width=8, height=6, seed=0) -> bytes:
    """아주 작은 합성 PNG(서명+IHDR 만 맞는 최소 형식이 아니라 실제로 열리는 PNG)."""
    import io
    from PIL import Image
    image = Image.new("L", (width, height), (seed * 37) % 256)
    image.putpixel((0, 0), 255 - (seed % 200))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class FakeNas:
    """로컬 가짜 NAS. routes: {경로: (상태, {헤더}, 바이트)}. 요청 기록과 지연 지정을 지원한다."""

    def __init__(self):
        self.routes = {}
        self.hits = []
        self.delay = {}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                time.sleep(outer.delay.get(self.path, 0))
                status, headers, body = outer.routes.get(self.path, (404, {}, b"not found"))
                self.send_response(status)
                omit_length = "X-Omit-Length" in headers   # 시험용: 길이를 알리지 않고 연결을 닫는 서버
                for key, value in headers.items():
                    if key != "X-Omit-Length":
                        self.send_header(key, value)
                if "Content-Length" not in headers and not omit_length:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):   # 시험 출력을 어지럽히지 않는다
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def serve_png(self, path, data=None, **headers):
        self.routes[path] = (200, {"Content-Type": "image/png", **headers}, data if data is not None else png())

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class BuildUrlTests(unittest.TestCase):
    BASE = "https://nas.example.test/mu_shop/public"

    def test_valid_paths_are_joined_and_quoted(self):
        self.assertEqual(nas.build_url(self.BASE, "/stylesheets/assets/knee/a.png"), self.BASE + "/stylesheets/assets/knee/a.png")
        self.assertEqual(nas.build_url(self.BASE + "/", "/s/한글 이름.png"), self.BASE + "/s/%ED%95%9C%EA%B8%80%20%EC%9D%B4%EB%A6%84.png")

    def test_unsafe_paths_are_rejected(self):
        for bad in ("", "relative/a.png", "//evil.example/a.png", "/a/../b.png", "/a/./b.png", "/a\\b.png", "/a?x=1", "/a#x",
                    "/a%2e%2e/b", "/a\x00b.png", "/a\nb.png", "http://evil.example/a.png", "/a://b", "/" + "x" * 1100, None, 5):
            with self.subTest(path=bad):
                with self.assertRaises(nas.AssetUnavailable):
                    nas.build_url(self.BASE, bad)

    def test_base_url_rules(self):
        for bad in ("", "ftp://nas.example.test", "http://nas.example.test", "https://user:pw@nas.example.test", "https://nas.example.test/?q=1",
                    "https://nas.example.test/#f", "nas.example.test", None):
            with self.subTest(base=bad):
                with self.assertRaises(nas.AssetUnavailable):
                    nas.build_url(bad, "/a.png")
        self.assertEqual(nas.build_url("http://127.0.0.1:9", "/a.png", allow_http=True), "http://127.0.0.1:9/a.png")   # 시험 전용


class PngSizeTests(unittest.TestCase):
    def test_reads_size_and_rejects_non_png(self):
        self.assertEqual(nas.png_size(png(8, 6)), (8, 6))
        for bad in (b"", b"not a png", png()[:20], b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, b"<html></html>" * 5):
            with self.subTest(bad=bad[:12]):
                with self.assertRaises(nas.AssetUnavailable):
                    nas.png_size(bad)

    def test_oversized_dimensions_are_rejected(self):
        import struct
        big = bytearray(png())
        big[16:24] = struct.pack(">II", 5000, 5000)
        with self.assertRaises(nas.AssetUnavailable):
            nas.png_size(bytes(big))


class DefaultFetchTests(unittest.TestCase):
    """실제 로컬 HTTP 서버로 가져오기 규칙을 확인한다."""

    @classmethod
    def setUpClass(cls):
        cls.nas = FakeNas()

    @classmethod
    def tearDownClass(cls):
        cls.nas.close()

    def setUp(self):
        self.nas.routes.clear()
        self.nas.hits.clear()
        self.nas.delay.clear()

    def fetch(self, path, **kw):
        return nas.default_fetch(self.nas.base + path, **kw)

    def test_success(self):
        data = png()
        self.nas.serve_png("/a.png", data)
        self.assertEqual(self.fetch("/a.png"), data)

    def test_status_and_content_type_rules(self):
        self.nas.routes["/missing.png"] = (404, {"Content-Type": "image/png"}, b"x")
        self.nas.routes["/server-error.png"] = (500, {"Content-Type": "image/png"}, b"x")
        self.nas.routes["/html.png"] = (200, {"Content-Type": "text/html"}, b"<html>")
        self.nas.routes["/nopng-type"] = (200, {}, png())
        for path in ("/missing.png", "/server-error.png", "/html.png", "/nopng-type"):
            with self.subTest(path=path):
                with self.assertRaises(nas.AssetUnavailable):
                    self.fetch(path)

    def test_redirects_are_not_followed(self):
        self.nas.routes["/hop.png"] = (302, {"Location": "/target.png"}, b"")
        self.nas.serve_png("/target.png")
        with self.assertRaises(nas.AssetUnavailable):
            self.fetch("/hop.png")
        self.assertNotIn("/target.png", self.nas.hits)                      # 따라가지 않았다

    def test_size_limit_declared_and_undeclared(self):
        self.nas.serve_png("/big.png", b"\x89PNG" + b"0" * 2000)
        with self.assertRaises(nas.AssetUnavailable):
            self.fetch("/big.png", max_bytes=1000)

    def test_size_limit_when_the_server_does_not_declare_a_length(self):
        self.nas.routes["/stream.png"] = (200, {"Content-Type": "image/png", "X-Omit-Length": "1"}, b"\x89PNG" + b"0" * 5000)
        with self.assertRaises(nas.AssetUnavailable):
            self.fetch("/stream.png", max_bytes=1000)
        self.nas.routes["/stream-ok.png"] = (200, {"Content-Type": "image/png", "X-Omit-Length": "1"}, b"\x89PNG" + b"0" * 100)
        self.assertEqual(len(self.fetch("/stream-ok.png", max_bytes=1000)), 104)

    def test_timeout_and_connection_failure(self):
        self.nas.serve_png("/slow.png")
        self.nas.delay["/slow.png"] = 1.5
        started = time.time()
        with self.assertRaises(nas.AssetUnavailable):
            self.fetch("/slow.png", timeout=0.3)
        self.assertLess(time.time() - started, 1.4)
        with self.assertRaises(nas.AssetUnavailable):
            nas.default_fetch("http://127.0.0.1:9/never.png", timeout=0.5)   # 닫힌 포트

    def test_error_text_has_no_url_or_body(self):
        self.nas.routes["/leak.png"] = (500, {"Content-Type": "text/plain"}, b"secret-internal-detail")
        try:
            self.fetch("/leak.png")
        except nas.AssetUnavailable as exc:
            self.assertNotIn("secret-internal-detail", str(exc))
            self.assertNotIn("127.0.0.1", str(exc))


class NasImageSourceTests(unittest.TestCase):
    def source(self, bodies, **kw):
        calls = []

        def fetch(url):
            calls.append(url)
            return bodies[url.rsplit("/", 1)[-1]]
        return nas.NasImageSource("https://nas.example.test/p", fetch=fetch, **kw), calls

    def test_matching_hash_returns_a_verified_image_and_is_cached(self):
        data = png(10, 4)
        source, calls = self.source({"a.png": data})
        sha = hashlib.sha256(data).hexdigest()
        first = source.get("/s/a.png", sha)
        self.assertEqual((first.width, first.height, first.sha256, first.data), (10, 4, sha, data))
        self.assertIs(source.get("/s/a.png", sha), first)
        self.assertEqual(len(calls), 1)                                      # 두 번째는 NAS 에 가지 않는다

    def test_hash_mismatch_is_refused_and_not_cached(self):
        source, calls = self.source({"a.png": png(8, 6, seed=1)})
        wrong = hashlib.sha256(png(8, 6, seed=2)).hexdigest()
        for _ in range(2):
            with self.assertRaises(nas.AssetMismatch):
                source.get("/s/a.png", wrong)
        self.assertEqual(len(calls), 2)                                      # 불일치 결과는 보관하지 않아 매번 다시 확인한다

    def test_bad_expected_hash_and_non_png_with_matching_hash(self):
        source, _ = self.source({"a.png": b"just text, not an image" * 3})
        for bad in ("", "abc", "G" * 64, "A" * 64, None):
            with self.assertRaises(nas.AssetUnavailable):
                source.get("/s/a.png", bad)
        with self.assertRaises(nas.AssetUnavailable):                        # 해시가 맞아도 PNG 가 아니면 보여 주지 않는다
            source.get("/s/a.png", hashlib.sha256(b"just text, not an image" * 3).hexdigest())

    def test_cache_keeps_at_most_the_configured_number(self):
        bodies = {f"{i}.png": png(8, 6, seed=i) for i in range(5)}
        source, calls = self.source(bodies, cache_items=2)
        for i in range(5):
            source.get(f"/s/{i}.png", hashlib.sha256(bodies[f"{i}.png"]).hexdigest())
        calls.clear()
        source.get("/s/0.png", hashlib.sha256(bodies["0.png"]).hexdigest())  # 밀려났으니 다시 가져온다
        self.assertEqual(len(calls), 1)

    def test_fetch_failures_surface_as_unavailable_only(self):
        def boom(url):
            raise OSError("secret host nas.internal")
        source = nas.NasImageSource("https://nas.example.test/p", fetch=boom)
        with self.assertRaises(OSError):                                      # 주입된 fetch 의 예외는 default_fetch 가 AssetUnavailable 로 바꾼다
            source.get("/s/a.png", "0" * 64)


class SourceFromSettingsTests(unittest.TestCase):
    def setUp(self):
        dotenv = mock.patch.object(knee_config, "ENV_PATH", Path(os.devnull) / "없음.env")
        dotenv.start()
        self.addCleanup(dotenv.stop)

    def settings(self, **env):
        base = {"KNEE_NAS_IMAGE_BASE_URL": "", "KNEE_NAS_ALLOW_HTTP": ""}
        base.update(env)
        patcher = mock.patch.dict(os.environ, base)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_closed_when_not_configured_or_malformed(self):
        for base in ("", "http://nas.example.test", "ftp://x", "https://u:p@nas.example.test", "not a url"):
            with self.subTest(base=base):
                with mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": base, "KNEE_NAS_ALLOW_HTTP": ""}):
                    self.assertIsNone(nas.source_from_settings())

    def test_https_base_gives_one_shared_source(self):
        with mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": "https://nas.example.test/p", "KNEE_NAS_ALLOW_HTTP": ""}):
            first = nas.source_from_settings()
            self.assertIsNotNone(first)
            self.assertIs(nas.source_from_settings(), first)                 # 캐시 공유
        with mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": "https://other.example.test/p", "KNEE_NAS_ALLOW_HTTP": ""}):
            self.assertIsNot(nas.source_from_settings(), first)

    def test_http_only_with_the_test_switch(self):
        with mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": "http://127.0.0.1:9", "KNEE_NAS_ALLOW_HTTP": "1"}):
            self.assertIsNotNone(nas.source_from_settings())


# ── 문제 제시·영상 주소 (임시 DB) ───────────────────────────────────────────────────
@unittest.skipUnless(HAVE_DB_TOOLS, "pgserver/psycopg/pillow 가 없어 임시 DB 시험을 건너뜀")
class NasConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from knee_screen_harness import Harness
        cls.h = Harness()
        cls.nas = FakeNas()

    @classmethod
    def tearDownClass(cls):
        cls.nas.close()
        cls.h.close()

    def setUp(self):
        self.client = TestClient(self.h.app, raise_server_exceptions=False)
        self.h.clock.offset = 0
        kneeLearning.set_asset_provider(None)                                 # 기본(NAS) 공급자를 쓴다
        self.addCleanup(kneeLearning.set_asset_provider,
                        lambda ref, manifest: {"image_url": f"/_images/rep-{ref.reference_id}.png", "image_width": 320, "image_height": 384})
        env = mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": self.nas.base, "KNEE_NAS_ALLOW_HTTP": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.nas.routes.clear()
        self.nas.hits.clear()
        for path, data in self.h.png_by_path.items():
            self.nas.serve_png(path, data)
        nas.source_from_settings().clear_cache()
        self.addCleanup(self.h.swap_reference, 12, reference_id=7, version="v1")
        self.assertEqual(self.client.post("/_login", params={"idx": self.h.user_idx}).status_code, 200)

    def task(self, expect=200):
        res = self.client.get("/learning/knee/task", params={"study_idx": self.h.study_idx})
        self.assertEqual(res.status_code, expect, res.text)
        return res

    def test_task_points_at_this_server_not_at_the_nas(self):
        res = self.task()
        task = res.json()["knee_task"]
        self.assertRegex(task["image_url"], rf"^/learning/knee/image/{self.h.study_idx}/[0-9a-f]{{16}}$")
        self.assertEqual((task["image_width"], task["image_height"]), (320, 384))
        self.assertEqual(task["image_url"].rsplit("/", 1)[1], task["presentation_id"])
        dumped = res.text
        for secret in (self.nas.base, "127.0.0.1", self.h.paths[12], "_knee_assets", self.h.rep_sha):
            self.assertNotIn(secret, dumped, secret)                         # NAS 주소·내부 경로·해시 비노출

    def test_image_is_served_from_verified_bytes(self):
        task = self.task().json()["knee_task"]
        res = self.client.get(task["image_url"])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, self.h.rep_png)
        self.assertEqual(hashlib.sha256(res.content).hexdigest(), self.h.rep_sha)
        self.assertEqual(res.headers["content-type"], "image/png")
        self.assertEqual(res.headers["x-content-type-options"], "nosniff")
        self.assertEqual(res.headers["cache-control"], "no-store")
        self.assertNotIn("attachment", res.headers.get("content-disposition", "inline"))
        self.assertNotIn(self.h.paths[12].rsplit("/", 1)[-1], str(res.headers))   # 파일 이름을 내려주지 않는다

    def test_repeated_opens_use_the_cache_not_the_nas(self):
        task = self.task().json()["knee_task"]
        self.client.get(task["image_url"])
        self.task()
        self.client.get(task["image_url"])
        self.assertEqual(len([h for h in self.nas.hits if h.endswith("/012.png")]), 1)

    def test_closed_doors_for_the_image_address(self):
        task = self.task().json()["knee_task"]
        url = task["image_url"]
        anonymous = TestClient(self.h.app, raise_server_exceptions=False)
        self.assertEqual(anonymous.get(url).status_code, 401)                # 비로그인
        self.assertEqual(self.client.post("/_login", params={"idx": self.h.user_idx, "verified": "false"}).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 403)              # 인증 안 됨
        self.client.post("/_login", params={"idx": self.h.gone_user_idx})
        self.assertEqual(self.client.get(url).status_code, 401)              # 삭제된 회원
        self.client.post("/_login", params={"idx": self.h.user_idx})
        with mock.patch.dict(os.environ, {"KNEE_ROI_GRADE_ENABLED": ""}):
            self.assertEqual(self.client.get(url).status_code, 503)          # 기능 꺼짐
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_wrong_study_or_presentation_is_not_found(self):
        task = self.task().json()["knee_task"]
        good = task["image_url"]
        for url in (f"/learning/knee/image/{self.h.study_idx}/{'0' * 16}", f"/learning/knee/image/{self.h.study_idx}/short",
                    f"/learning/knee/image/{self.h.unapproved_study_idx}/{task['presentation_id']}",
                    f"/learning/knee/image/{self.h.brain_study_idx}/{task['presentation_id']}",
                    f"/learning/knee/image/99999/{task['presentation_id']}",
                    f"/learning/knee/image/{self.h.study_idx}/{task['presentation_id'].upper()}"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(good).status_code, 200)

    def test_old_address_stops_working_when_the_approval_changes(self):
        old = self.task().json()["knee_task"]["image_url"]
        self.assertEqual(self.client.get(old).status_code, 200)
        self.h.swap_reference(13, reference_id=8, version="v2")
        self.assertEqual(self.client.get(old).status_code, 404)              # 대표가 바뀌면 옛 주소는 막힌다
        new = self.task().json()["knee_task"]
        self.assertNotEqual(new["image_url"], old)
        res = self.client.get(new["image_url"])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, self.h.png_by_path[self.h.paths[13]])

    def test_content_that_differs_from_the_approved_hash_is_never_served_or_presented(self):
        self.nas.serve_png(self.h.paths[12], png(320, 384, seed=99))         # NAS 의 파일이 바뀌었다
        res = self.task(503)
        self.assertNotIn("problem_token", res.text)
        self.assertNotIn(self.nas.base, res.text)
        task_url = f"/learning/knee/image/{self.h.study_idx}/{self.pid()}"
        self.assertEqual(self.client.get(task_url).status_code, 503)

    def pid(self):
        from services import kneeSliceGradeServices as service
        key = knee_config.problem_token_key()
        return service.presentation_id(key, study_idx=self.h.study_idx, manifest=self.h.manifest, reference=self.h.ref)

    def test_already_verified_bytes_survive_a_later_tampering(self):
        task = self.task().json()["knee_task"]
        self.assertEqual(self.client.get(task["image_url"]).content, self.h.rep_png)
        self.nas.serve_png(self.h.paths[12], png(320, 384, seed=99))
        self.assertEqual(self.client.get(task["image_url"]).content, self.h.rep_png)   # 승인된 해시로 검증된 바이트만 계속 보여 준다

    def test_nas_outage_is_a_fixed_503_without_details(self):
        self.nas.routes.clear()                                              # NAS 가 404/장애
        res = self.task(503)
        self.assertNotIn(self.nas.base, res.text)
        self.assertNotIn("127.0.0.1", res.text)
        self.nas.routes[self.h.paths[12]] = (500, {"Content-Type": "text/plain"}, b"nas internal error details")
        res = self.client.get(f"/learning/knee/image/{self.h.study_idx}/{self.pid()}")
        self.assertEqual(res.status_code, 503)
        self.assertNotIn("nas internal", res.text)

    def test_closed_when_the_nas_base_is_not_configured(self):
        with mock.patch.dict(os.environ, {"KNEE_NAS_IMAGE_BASE_URL": ""}):
            self.task(503)
            self.assertEqual(self.client.get(f"/learning/knee/image/{self.h.study_idx}/{self.pid()}").status_code, 503)

    def test_unapproved_study_never_touches_the_nas(self):
        self.nas.hits.clear()
        task = self.client.get("/learning/knee/task", params={"study_idx": self.h.unapproved_study_idx}).json()["knee_task"]
        self.assertFalse(task["grading_available"])
        self.assertEqual(self.nas.hits, [])

    def test_full_flow_login_task_image_grade(self):
        task = self.task().json()["knee_task"]
        self.assertEqual(self.client.get(task["image_url"]).status_code, 200)
        res = self.client.post("/learning/roi-grade/knee", data={
            "study_idx": str(self.h.study_idx), "roi_type": "box", "x": "0.3", "y": "0.3", "width": "0.4", "height": "0.4",
            "slice_index": str(task["representative_slice_index"]), "problem_token": task["problem_token"]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["graded"])
        self.assertEqual(self.h.reviews()[-1].rn_image, self.h.paths[12])


if __name__ == "__main__":
    unittest.main()
