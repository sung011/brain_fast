"""
무릎 화면 분석(넘기며 분석) API 시험 — MSN-20261021-001 v2 M02~M05·M08.
라우터만 올린 작은 앱으로 시험한다(DB·NAS·뇌 모델 없음). 실제 모델 시험은 이 PC 의 HF 캐시·로컬 KneeMRI PNG 가 있을 때만 돈다.
실행: python -m pytest tests/test_knee_screen_analysis.py
"""
import asyncio
import io
import json
import os
import sys
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

router_mod = import_module("routers.kneeScreenAnalysis")
sa = import_module("class.knee.scroll_analysis")
acl_mod = import_module("class.knee.acl_volume")
errors = import_module("class.knee.errors")

DATA = Path(r"C:\Users\user\Desktop\메디스캔노트(깃허브)\knee_series_data")
MANIFEST = DATA / "knee_series" / "cases_manifest_case-002_case-908.json"


def png(arr):
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def frames(n, size=(128, 128), dup_every=None):
    out = []
    for i in range(n):
        k = i if not dup_every else (i // dup_every)
        a = np.full((size[1], size[0]), 40, np.uint8)
        a[10 + k: 30 + k, 20:60] = 200
        out.append(png(a))
    return out


def manifest(n, intent="scroll", **over):
    m = {"schema_version": 1, "plane": "sagittal", "image_scope": "full_slice", "capture_intent": intent, "capture_profile_id": "capture_profile_v1",
         "frames": [{"sequence": i, "file_index": i, "captured_at_ms": i * 300} for i in range(n)]}
    m.update(over)
    return json.dumps(m)


class FakeModel:
    model_id, model_sha256, device = "fake@000", "0" * 64, "cpu"

    def __init__(self, raise_exc=None):
        self.calls = []
        self.overlay_flags = []
        self.raise_exc = raise_exc

    def analyze(self, grays, include_overlay=True):
        self.calls.append(len(grays))
        self.overlay_flags.append(include_overlay)
        if self.raise_exc:
            raise self.raise_exc
        # 예전 볼륨 경로처럼 CAM 그림을 같이 돌려줘도 넘기며 분석 응답은 이를 쓰면 안 된다(CAM_LOCALIZATION_STEP2 AC02)
        return {"raw_score": 0.873, "cam_index": 2, "overlay": Image.new("RGB", (224, 224), (255, 90, 60)), "activated": True}


loc_mod = import_module("class.knee.screen_localization")
FIXED_DET = {"x_center": 0.5, "y_center": 0.4, "width": 0.3, "height": 0.2, "confidence": 0.9}


class FakeDetect:
    """위치 모델 대역: 받은 회색 영상을 기록하고 정해진 박스(또는 None·예외)를 돌려준다."""
    def __init__(self, out=FIXED_DET, exc=None):
        self.seen, self.out, self.exc = [], out, exc

    def __call__(self, gray):
        self.seen.append(np.array(gray, copy=True))
        if self.exc:
            raise self.exc
        return self.out


class ApiTests(unittest.TestCase):
    def setUp(self):
        enabled = mock.patch.dict(os.environ, {"KNEE_SCREEN_LOCALIZATION_ENABLED": "1"})
        enabled.start()
        self.addCleanup(enabled.stop)
        app = FastAPI()
        app.include_router(router_mod.router, prefix="/api/v1")
        self.client = TestClient(app)
        self.fake = FakeModel()
        p = mock.patch.object(acl_mod, "acl_volume", self.fake)
        p.start()
        self.addCleanup(p.stop)
        self.det = FakeDetect()
        q = mock.patch.object(loc_mod.locator, "detect", self.det)
        q.start()
        self.addCleanup(q.stop)

    def post(self, blobs, man, extra=None):
        files = [("frames", (f"f{i:03d}.png", b, "image/png")) for i, b in enumerate(blobs)]
        data = {"manifest": man}
        data.update(extra or {})
        return self.client.post("/api/v1/knee/screen-analysis", files=files, data=data)

    def test_scroll_15_real_score_contract(self):
        r = self.post(frames(15), manifest(15))
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertEqual((b["model_kind"], b["analysis_status"], b["usage_mode"], b["evaluation_class"]), ("volume", "evaluated", "learning_demo", "exploratory"))
        self.assertEqual((b["capture"]["received_count"], b["capture"]["used_count"]), (15, 15))
        self.assertEqual((b["acl"]["raw_score"], b["acl"]["score_kind"], b["acl"]["decision"], b["acl"]["calibrated_score"]), (0.873, "raw", "score_only", None))
        self.assertIn("87.3/100", b["acl"]["display_text"])
        v = b["visualization"]
        self.assertEqual((v["status"], v["kind"], v["overlay_frame_sequence"]), ("shown", "anatomical_box", 7))   # 15장 → 가운데 8번째(순번 7), CAM 의 2 아님
        loc = v["localization"]
        self.assertEqual((loc["status"], loc["frame_ordinal"], loc["frame_sequence"], loc["frame_selection"]), ("shown", 8, 7, "middle_used_frame"))
        self.assertEqual((loc["frame_width"], loc["frame_height"], loc["coordinate_space"]), (128, 128, "captured_frame_normalized"))
        self.assertEqual(loc["box"], {"x": 0.35, "y": 0.3, "width": 0.3, "height": 0.2})
        self.assertEqual((loc["model_id"], loc["model_sha256"], loc["evidence_status"]), ("acl_yolo", loc_mod.YOLO_SHA256, "training_selection_exposed_not_capture_validated"))
        self.assertNotIn("_png", loc)
        self.assertNotIn("confidence", json.dumps(loc))                           # 위치 모델 확신도를 화면용 값으로 내지 않음
        png_ = Image.open(io.BytesIO(__import__("base64").b64decode(v["overlay_png_base64"]))).convert("RGB")
        self.assertEqual(png_.size, (128, 128))                                   # 원 캡처 크기(≤512) — CAM 224 그림 아님
        self.assertFalse((np.asarray(png_) == (255, 90, 60)).all(axis=-1).any())  # 볼륨 CAM 그림은 어디에도 안 섞임
        self.assertEqual(len(self.det.seen), 1)                                   # 가운데 한 장 1회만
        self.assertTrue(np.array_equal(self.det.seen[0], np.asarray(Image.open(io.BytesIO(frames(15)[7])))))
        self.assertEqual(self.fake.overlay_flags, [False])                         # 볼륨 CAM 계산 0
        self.assertIn("열지도는 ACL 위치와 일치가 낮아", b["explanations"][0]["text"])
        self.assertIn("8번째 장(수집 순번 7)", b["explanations"][0]["text"])
        self.assertFalse(b["evidence"]["independent_test"])
        self.assertFalse(b["explanations"][0]["clinical_review"])
        self.assertEqual(self.fake.calls, [15])                                  # 여러 장을 한 번에(장별 호출 아님)
        self.assertEqual(b["warnings"], [])

    def test_location_middle_of_used_list_with_gaps_and_counts(self):
        # 수집 순번에 빈 번호·중복이 있어도 서버 사용 목록 U 의 가운데 장 그대로(같은 장의 그림·순번·ordinal)
        blobs = frames(12) + frames(12)[:3]                                       # 받은 15, 고유 12
        m = json.loads(manifest(15)); seqs = [0, 2, 3, 5, 8, 9, 11, 14, 15, 16, 20, 21, 30, 31, 32]
        for f, s in zip(m["frames"], seqs):
            f["sequence"], f["captured_at_ms"] = s, s * 300
        b = self.post(blobs, json.dumps(m)).json()
        loc = b["visualization"]["localization"]
        self.assertEqual((b["capture"]["used_count"], loc["frame_ordinal"], loc["frame_sequence"]), (12, 7, seqs[6]))
        self.assertTrue(np.array_equal(self.det.seen[-1], np.asarray(Image.open(io.BytesIO(frames(12)[6])))))
        for n, ordinal in ((10, 6), (20, 11)):
            loc = self.post(frames(n), manifest(n)).json()["visualization"]["localization"]
            self.assertEqual((loc["frame_ordinal"], loc["frame_sequence"]), (ordinal, ordinal - 1))

    def test_location_withheld_cases_keep_score(self):
        cases = [(FakeDetect(out=None), "not_found"), (FakeDetect(exc=loc_mod.LocationUnavailable("cache_missing")), "unavailable"),
                 (FakeDetect(exc=RuntimeError("boom /secret/path")), "unavailable"),
                 (FakeDetect(out={"x_center": float("nan"), "y_center": .5, "width": .2, "height": .2}), "invalid_box"),
                 (FakeDetect(out={"x_center": .95, "y_center": .5, "width": .3, "height": .2}), "invalid_box"),
                 (FakeDetect(out={"x_center": .5, "y_center": .5, "width": 0.0, "height": .2}), "invalid_box")]
        for det, status in cases:
            with mock.patch.object(loc_mod.locator, "detect", det):
                r = self.post(frames(15), manifest(15))
            self.assertEqual(r.status_code, 200, status)
            b = r.json(); v = b["visualization"]
            self.assertEqual((v["status"], v["kind"], v["overlay_png_base64"], v["overlay_frame_sequence"]), ("withheld", "none", None, None), status)
            self.assertEqual((v["localization"]["status"], v["localization"]["box"]), (status, None))
            self.assertEqual((b["acl"]["raw_score"], b["analysis_status"]), (0.873, "evaluated"))     # 분류 결과는 그대로
            self.assertNotIn("secret", r.text)                                    # 예외 원문·경로 비노출
            self.assertEqual(len(det.seen), 1)                                    # 다른 장으로 다시 찾지 않음
            if status == "not_found":
                self.assertIn("정상이라는 뜻은 아닙니다", v["note"])
                self.assertIn("정상이라는 뜻은 아닙니다", b["explanations"][0]["text"])

    def test_location_not_requested_and_roi_crop(self):
        b = self.post(frames(15), manifest(15), {"include_overlay": "false"}).json()
        v = b["visualization"]
        self.assertEqual((v["status"], v["kind"], v["localization"]["status"], v["overlay_png_base64"]), ("not_requested", "none", "not_requested", None))
        self.assertEqual((self.det.seen, self.fake.overlay_flags[-1]), ([], False))   # YOLO·CAM 모두 0
        b2 = self.post(frames(15), manifest(15, image_scope="roi_crop")).json()
        v2 = b2["visualization"]
        self.assertEqual((v2["status"], v2["localization"]["status"], v2["overlay_png_base64"]), ("withheld", "unsupported_scope", None))
        self.assertEqual(self.det.seen, [])                                       # 부분 캡처에는 YOLO 를 쓰지 않음
        self.assertEqual(b["acl"]["raw_score"], b2["acl"]["raw_score"])

    def test_location_server_off_keeps_score_and_single_cam(self):
        single_mod = import_module("class.knee.single_image")
        class Single(FakeModel):
            def configured(self):
                return True
        single = Single()
        for value in ("0", "", "unexpected"):
            with mock.patch.dict(os.environ, {"KNEE_SCREEN_LOCALIZATION_ENABLED": value}):
                b = self.post(frames(15), manifest(15)).json()
                self.assertEqual(b["acl"]["raw_score"], .873)
                self.assertTrue(b["explanations"])
                self.assertEqual(b["visualization"]["localization"]["status"], "disabled_by_policy")
                self.assertIsNone(b["visualization"]["overlay_png_base64"])
                with mock.patch.object(single_mod, "acl_single", single):
                    s = self.post(frames(1), manifest(1, intent="single")).json()
                self.assertEqual((s["model_kind"], s["visualization"]["kind"]), ("single_2d", "grad_cam"))
                self.assertEqual(s["acl"]["raw_score"], .873)
                self.assertEqual(s["visualization"]["status"], "shown")
        self.assertEqual(self.det.seen, [])
        with mock.patch.dict(os.environ, {"KNEE_SCREEN_LOCALIZATION_ENABLED": "0"}):
            r = self.post(frames(15), manifest(15), {"KNEE_SCREEN_LOCALIZATION_ENABLED": "1"})
        self.assertEqual(r.status_code, 400)

    def test_ten_ok_with_warning_and_twenty_max(self):
        b = self.post(frames(10), manifest(10)).json()
        self.assertEqual(b["capture"]["used_count"], 10)
        self.assertEqual(b["warnings"][0]["code"], "below_recommended")
        self.assertEqual(self.post(frames(20), manifest(20)).status_code, 200)
        self.assertEqual(self.post(frames(21), manifest(21)).status_code, 413)

    def test_duplicates_removed_by_pixels_then_insufficient(self):
        r = self.post(frames(15, dup_every=2), manifest(15))                     # 고유 8장
        self.assertEqual((r.status_code, r.json()["code"]), (422, "insufficient_frames"))
        b = self.post(frames(12) + frames(12)[:3], manifest(15)).json()          # 고유 12, 중복 3
        self.assertEqual((b["capture"]["received_count"], b["capture"]["used_count"], b["warnings"][-1]["code"]), (15, 12, "duplicates_removed"))
        self.assertEqual(self.post(frames(9), manifest(9)).json()["code"], "insufficient_frames")

    def test_single_dispatch_never_falls_back_to_volume(self):
        r = self.post(frames(1), manifest(1, intent="single"))
        self.assertEqual((r.status_code, r.json()["code"]), (503, "model_single_not_ready"))
        self.assertEqual(self.post(frames(3), manifest(3, intent="single")).json()["code"], "single_needs_one")
        self.assertEqual(self.fake.calls, [])

    def test_manifest_and_field_rejections(self):
        f = frames(10)
        bad = {
            "extra field": (f, manifest(10), {"score": "0.9"}),
            "extra manifest key": (f, json.dumps(dict(json.loads(manifest(10)), model_name="x")), None),
            "count mismatch": (f, manifest(9), None),
            "plane": (f, manifest(10, plane="coronal"), None),
            "scope": (f, manifest(10, image_scope="whole"), None),
            "schema": (f, manifest(10, schema_version=2), None),
            "not json": (f, "{bad", None),
            "nan": (f, manifest(10).replace('"captured_at_ms": 0', '"captured_at_ms": NaN'), None),
        }
        m = json.loads(manifest(10)); m["frames"][3]["sequence"] = 1
        bad["sequence not increasing"] = (f, json.dumps(m), None)
        m = json.loads(manifest(10)); m["frames"][0]["file_index"] = 5
        bad["file index"] = (f, json.dumps(m), None)
        m = json.loads(manifest(10)); m["frames"][0]["captured_at_ms"] = -1
        bad["negative time"] = (f, json.dumps(m), None)
        for name, (blobs, man, extra) in bad.items():
            self.assertEqual(self.post(blobs, man, extra).status_code, 400, name)
        r = self.client.post("/api/v1/knee/screen-analysis", files=[("frames", ("a.png", f[0], "image/png"))])
        self.assertEqual(r.status_code, 400)                                      # manifest 없음
        self.assertEqual(self.fake.calls, [])

    def test_image_rejections(self):
        mixed = frames(9) + frames(1, size=(140, 128))
        self.assertEqual(self.post(mixed, manifest(10)).json()["code"], "mixed_frame_size")
        self.assertEqual(self.post(frames(10, size=(600, 128)), manifest(10)).json()["code"], "frame_size")
        self.assertEqual(self.post([b"notimage"] * 10, manifest(10)).status_code, 415)
        big = np.random.default_rng(0).integers(0, 255, (512, 512), dtype=np.uint8)
        noisy = [png(np.roll(big, i, axis=0)) for i in range(10)]
        if len(noisy[0]) > sa.MAX_FRAME_BYTES:
            self.assertEqual(self.post(noisy, manifest(10)).status_code, 413)

    def test_model_errors_are_technical_errors_not_scores(self):
        for exc, status in ((errors.KneeModelNotReady("x"), 503), (errors.KneeInputError("x"), 422)):
            self.fake.raise_exc = exc
            r = self.post(frames(10), manifest(10))
            self.assertEqual(r.status_code, status)
            self.assertNotIn("acl", r.json())

    def test_busy_returns_429(self):
        self.assertTrue(router_mod._BUSY.try_acquire())
        try:
            r = self.post(frames(10), manifest(10))
            self.assertEqual(r.status_code, 429)
        finally:
            router_mod._BUSY.release()
        self.assertEqual(self.post(frames(10), manifest(10)).status_code, 200)

    def test_timeout_keeps_slot_until_worker_finishes(self):
        """504 뒤에도 계산 스레드가 끝날 때까지 실행권 유지 → 다음 요청 429, 끝나면 다시 200(Codex STEP2-001)."""
        import threading
        import time as _t
        done = threading.Event()

        class Slow(FakeModel):
            def analyze(self, grays, include_overlay=True):
                self.calls.append(len(grays))
                _t.sleep(0.6)
                done.set()
                return super().analyze(grays, include_overlay)

        slow = Slow()
        import httpx

        def files():
            return [("frames", (f"f{i:03d}.png", b, "image/png")) for i, b in enumerate(frames(10))]

        async def scenario():
            app = FastAPI()
            app.include_router(router_mod.router, prefix="/api/v1")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:   # 한 이벤트 루프(실제 서버와 같음)
                r1 = await c.post("/api/v1/knee/screen-analysis", files=files(), data={"manifest": manifest(10)})
                r2 = await c.post("/api/v1/knee/screen-analysis", files=files(), data={"manifest": manifest(10)})
                calls_at_r2 = len(slow.calls)
                for _ in range(100):
                    if done.is_set() and not router_mod._BUSY.locked():
                        break
                    await asyncio.sleep(0.02)
                locked_after = router_mod._BUSY.locked()
                return r1.status_code, r2.status_code, calls_at_r2, locked_after

        with mock.patch.object(acl_mod, "acl_volume", slow), \
                mock.patch.object(router_mod, "_setting", lambda k, d: "0.05" if k == "KNEE_SCREEN_ANALYSIS_TIMEOUT_S" else d):
            s1, s2, calls, locked_after = asyncio.run(scenario())
        self.assertEqual((s1, s2), (504, 429))
        self.assertEqual(calls, 1)                       # 두 번째 요청 때 계산은 첫 번째 1회뿐(쌓이지 않음)
        self.assertFalse(locked_after)                   # 계산이 끝나면 실행권이 풀림
        self.assertEqual(self.post(frames(10), manifest(10)).status_code, 200)

    def test_body_limit_before_multipart_parsing(self):
        """Content-Length 없는 큰 본문: 한도를 넘는 순간 413, 나머지는 읽지 않고 파서 임시 파일에 쓰지 않음(Codex STEP2-002)."""
        app = FastAPI()
        app.include_router(router_mod.router, prefix="/api/v1")
        pulled = {"bytes": 0, "chunks": 0}
        head = (b'--b\r\nContent-Disposition: form-data; name="manifest"\r\n\r\n' + manifest(1, intent="single").encode() +
                b'\r\n--b\r\nContent-Disposition: form-data; name="frames"; filename="x.png"\r\nContent-Type: image/png\r\n\r\n')
        chunks = [head] + [b"x" * (1024 * 1024)] * 20 + [b"\r\n--b--\r\n"]

        async def receive():
            if pulled["chunks"] < len(chunks):
                c = chunks[pulled["chunks"]]
                pulled["chunks"] += 1
                pulled["bytes"] += len(c)
                return {"type": "http.request", "body": c, "more_body": pulled["chunks"] < len(chunks)}
            return {"type": "http.disconnect"}

        sent = {}

        async def send(msg):
            if msg["type"] == "http.response.start":
                sent["status"] = msg["status"]

        import starlette.formparsers as fp
        writes = {"n": 0}
        orig = fp.SpooledTemporaryFile

        def spool(*a, **k):
            f = orig(*a, **k)
            w = f.write
            f.write = lambda b: (writes.__setitem__("n", writes["n"] + len(b)), w(b))[1]
            return f

        scope = {"type": "http", "method": "POST", "path": "/api/v1/knee/screen-analysis", "raw_path": b"/api/v1/knee/screen-analysis",
                 "query_string": b"", "headers": [(b"content-type", b"multipart/form-data; boundary=b")], "http_version": "1.1",
                 "scheme": "http", "server": ("t", 80), "client": ("c", 1), "root_path": ""}
        with mock.patch.object(fp, "SpooledTemporaryFile", spool):
            asyncio.run(app(scope, receive, send))
        self.assertEqual(sent["status"], 413)
        self.assertLess(pulled["chunks"], len(chunks))                               # 끝까지 읽지 않음
        self.assertLessEqual(pulled["bytes"], sa.MAX_TOTAL_BYTES + router_mod.BODY_OVERHEAD + 1024 * 1024 + len(head))
        self.assertEqual(writes["n"], 0)                                              # 파서 임시 파일 기록 없음

    def test_manifest_strict_types_and_time_order(self):
        f = frames(10)
        m = json.loads(manifest(10)); m["schema_version"] = True
        self.assertEqual(self.post(f, json.dumps(m)).status_code, 400)             # true(=1) 거절
        m = json.loads(manifest(10)); m["frames"][5]["captured_at_ms"] = 10
        self.assertEqual(self.post(f, json.dumps(m)).status_code, 400)             # 시각 감소 거절


def _local_case():
    if not MANIFEST.exists():
        return None
    cases = {c["case_code"]: c for c in json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"]}
    c = cases.get("case-456")
    return c if c and (DATA / c["paths"][0].lstrip("/")).exists() else None


def _cached():
    try:
        from huggingface_hub import try_to_load_from_cache
        cfg = import_module("class.knee.config")
        p = try_to_load_from_cache(cfg.hf_repo(), "best_acl_combined.pt", revision=cfg.hf_revision())
        return isinstance(p, str)
    except Exception:
        return False


@unittest.skipUnless(_local_case() and _cached(), "로컬 KneeMRI PNG 또는 HF 캐시 가중치 없음")
class RealModelTests(unittest.TestCase):
    """실제 best_acl_combined 로 1케이스 15장(가운데) — 오프라인 탐색 경로와 같은 점수, 장별 평균 아님, CAM 결속."""

    @classmethod
    def setUpClass(cls):
        os.environ["HF_HUB_OFFLINE"] = "1"
        p = mock.patch.dict(os.environ, {"KNEE_SCREEN_LOCALIZATION_ENABLED": "1"})
        p.start()
        cls.addClassCleanup(p.stop)
        c = _local_case()
        mid = len(c["paths"]) // 2
        cls.blobs = [(DATA / p.lstrip("/")).read_bytes() for p in c["paths"][mid - 7: mid + 8]]
        app = FastAPI()
        app.include_router(router_mod.router, prefix="/api/v1")
        cls.client = TestClient(app)

    def test_real_score_matches_volume_path_and_cam(self):
        files = [("frames", (f"f{i:03d}.png", b, "image/png")) for i, b in enumerate(self.blobs)]
        r = self.client.post("/api/v1/knee/screen-analysis", files=files, data={"manifest": manifest(15)})
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        raw = b["acl"]["raw_score"]
        self.assertTrue(0.0 <= raw <= 1.0)
        imageio = import_module("class.knee.imageio")
        grays = [imageio.decode_screen_image(x).gray for x in self.blobs]
        model = acl_mod.acl_volume
        direct = model.analyze(grays, include_overlay=False)["raw_score"]       # 같은 모델·같은 입력, 히트맵 없이
        self.assertAlmostEqual(raw, round(direct, 6), places=5)                   # 분류 점수 = CAM 과 같은 forward 점수
        singles = [model.analyze([g], include_overlay=False)["raw_score"] for g in grays]
        self.assertGreater(abs(raw - float(np.mean(singles))), 1e-4)             # 장별 평균으로 대체하지 않음
        with_cam = model.analyze(grays, include_overlay=True)["raw_score"]       # 예전 경로(CAM 과 같은 forward) 점수와도 같음(AC01)
        self.assertLessEqual(abs(raw - with_cam), 1e-5)
        v = b["visualization"]
        self.assertIn((v["status"], v["kind"]), (("shown", "anatomical_box"), ("withheld", "none")))
        self.assertEqual(v["localization"]["frame_sequence"], 7)
        if v["status"] == "shown":
            self.assertEqual(v["overlay_frame_sequence"], 7)
        self.assertEqual(b["model_sha256"], model.model_sha256)


if __name__ == "__main__":
    unittest.main()
