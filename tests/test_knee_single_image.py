"""
무릎 한 장 2D 모델(single_2d) 연결 시험 — MSN-20261021-001 SINGLE_2D_CONNECTION_PLAN SINGLE01~05.
라우터만 올린 작은 앱(DB·NAS·뇌 모델 없음). 실제 모델 시험은 이 PC 의 반입본(colab_import/v1)과 고정 fixture 가 있을 때만 돈다.
실행: python -m pytest tests/test_knee_single_image.py
"""
import asyncio
import io
import json
import shutil
import sys
import tempfile
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
single_mod = import_module("class.knee.single_image")
acl_mod = import_module("class.knee.acl_volume")
errors = import_module("class.knee.errors")
imageio = import_module("class.knee.imageio")

BUNDLE = Path(r"C:\Users\user\Desktop\setting\designs\MSN-20261021-001\colab_import\v1")
FIXTURE = Path(r"C:\Users\user\Desktop\setting\designs\MSN-20261021-001\single2d_fixture")
HAVE_REAL = BUNDLE.is_dir() and (FIXTURE / "fixture.json").exists()


def png(arr):
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def frame(k=0, size=(128, 128)):
    a = np.full((size[1], size[0]), 40, np.uint8)
    a[10 + k: 30 + k, 20:60] = 200
    return png(a)


def manifest(n, intent="single", **over):
    m = {"schema_version": 1, "plane": "sagittal", "image_scope": "full_slice", "capture_intent": intent, "capture_profile_id": "capture_profile_v1",
         "frames": [{"sequence": i, "file_index": i, "captured_at_ms": i * 300} for i in range(n)]}
    m.update(over)
    return json.dumps(m)


class FakeVolume:
    model_id, model_sha256, device = "fakevol@000", "0" * 64, "cpu"

    def __init__(self):
        self.calls = []

    def analyze(self, grays, include_overlay=True):
        self.calls.append(len(grays))
        return {"raw_score": 0.5, "cam_index": 0, "overlay": None, "activated": False}


class FakeSingle:
    model_id, model_sha256, device = "fakesingle@000", "1" * 64, "cpu"

    def __init__(self, configured=True, raise_exc=None, delay=0.0):
        self.calls, self._configured, self.raise_exc, self.delay = [], configured, raise_exc, delay

    def configured(self):
        return self._configured

    def analyze(self, grays, include_overlay=True):
        import time
        self.calls.append(len(grays))
        if self.delay:
            time.sleep(self.delay)
        if self.raise_exc:
            raise self.raise_exc
        return {"raw_score": 0.724, "logit": 0.965, "cam_index": 0, "overlay": Image.new("RGB", (224, 224)), "activated": True}


def _flip(b, at=None):
    at = len(b) // 2 if at is None else at
    return b[:at] + bytes([b[at] ^ 0xFF]) + b[at + 1:]


def _gray(path):
    """서버 디코드(imageio). 상수(빈) 영상은 서버가 422 로 거절하므로 모델 계산 대조만 PIL 회색 변환으로 한다."""
    data = path.read_bytes()
    try:
        return imageio.decode_screen_image(data).gray, None
    except imageio.ImageRejected as exc:
        return np.asarray(Image.open(io.BytesIO(data)).convert("L"), dtype=np.uint8), exc.code


def _client():
    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    return TestClient(app)


def _post(client, blobs, man):
    files = [("frames", (f"f{i:03d}.png", b, "image/png")) for i, b in enumerate(blobs)]
    return client.post("/api/v1/knee/screen-analysis", files=files, data={"manifest": man})


class DispatchTests(unittest.TestCase):
    """SINGLE03·04·05(가짜 모델): 한 장=single_2d, 여러 장=volume, 대신 쓰기 없음, 응답 계약, 상한·실행권."""

    def setUp(self):
        self.client = _client()
        self.vol, self.single = FakeVolume(), FakeSingle()
        for target, obj in ((acl_mod, ("acl_volume", self.vol)), (single_mod, ("acl_single", self.single))):
            p = mock.patch.object(target, obj[0], obj[1])
            p.start()
            self.addCleanup(p.stop)

    def test_single_uses_2d_model_with_raw_score_only_contract(self):
        r = _post(self.client, [frame()], manifest(1))
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertEqual((b["model_kind"], b["profile_id"], b["model_id"], b["model_sha256"]), ("single_2d", "knee_single_demo_v1", "fakesingle@000", "1" * 64))
        a = b["acl"]
        self.assertEqual((a["raw_score"], a["display_score"], a["score_kind"], a["decision"], a["band"], a["calibrated_score"], a["calibration_id"]),
                         (0.724, 0.724, "raw", "score_only", None, None, None))
        self.assertIn("72.4/100", a["display_text"])
        self.assertEqual((b["capture"]["received_count"], b["capture"]["unique_count"], b["capture"]["used_count"]), (1, 1, 1))
        self.assertEqual((b["capture"]["frame_width"], b["capture"]["frame_height"], b["capture"]["scope"]), (128, 128, "full_slice"))
        self.assertEqual((b["visualization"]["status"], b["visualization"]["overlay_frame_sequence"]), ("shown", 0))
        ev = b["evidence"]
        self.assertFalse(ev["independent_test"])
        self.assertEqual((ev["score_policy"], ev["calibration_profile"], ev["operating_point"]["met"], ev["operating_point"]["used_for_decision"]), ("raw_only", None, False, False))
        self.assertEqual(ev["internal_holdout"]["groups"], 181)
        self.assertFalse(b["explanations"][0]["clinical_review"])
        self.assertIn("한 장", b["explanations"][0]["text"])
        self.assertNotIn("수집한", b["explanations"][0]["text"])                 # 여러 장 해설을 그대로 복사하지 않음
        text = json.dumps(b, ensure_ascii=False)
        for banned in ("정확도 90", "90.1%", "확률 72", "partial", "complete", "C:\\", "Users"):
            self.assertNotIn(banned, text)
        self.assertEqual((self.single.calls, self.vol.calls), ([1], []))

    def test_scroll_still_volume_and_no_fallback_either_way(self):
        r = _post(self.client, [frame(k) for k in range(10)], manifest(10, intent="scroll"))
        self.assertEqual((r.status_code, r.json()["model_kind"]), (200, "volume"))
        r = _post(self.client, [frame()] * 10, manifest(10, intent="scroll"))   # 중복 제거 뒤 1장 → 부족, 한 장 모델로 몰래 바꾸지 않음
        self.assertEqual((r.status_code, r.json()["code"]), (422, "insufficient_frames"))
        r = _post(self.client, [frame(k) for k in range(5)], manifest(5, intent="scroll"))
        self.assertEqual(r.json()["code"], "insufficient_frames")
        self.assertEqual(_post(self.client, [frame(0), frame(1)], manifest(2)).json()["code"], "single_needs_one")
        self.assertEqual((self.vol.calls, self.single.calls), ([10], []))

    def test_not_configured_or_load_failure_is_503_without_volume(self):
        self.single._configured = False
        r = _post(self.client, [frame()], manifest(1))
        self.assertEqual((r.status_code, r.json()["code"]), (503, "model_single_not_ready"))
        self.single._configured = True
        self.single.raise_exc = errors.KneeModelNotReady("secret path C:\\x")
        r = _post(self.client, [frame()], manifest(1))
        self.assertEqual((r.status_code, r.json()["code"]), (503, "model_single_not_ready"))
        self.assertNotIn("secret", r.text)
        self.assertNotIn("acl", r.json())
        self.assertEqual(self.vol.calls, [])
        self.assertFalse(router_mod._BUSY.locked())

    def test_input_errors_and_scope_plane(self):
        self.single.raise_exc = errors.KneeInputError("x")
        self.assertEqual(_post(self.client, [frame()], manifest(1)).status_code, 422)
        self.single.raise_exc = None
        self.assertEqual(_post(self.client, [frame()], manifest(1, plane="coronal")).status_code, 400)
        self.assertEqual(_post(self.client, [frame(size=(40, 128))], manifest(1)).json()["code"], "image_too_small")
        b = _post(self.client, [frame()], manifest(1, image_scope="roi_crop")).json()
        self.assertEqual((b["capture"]["scope"], b["warnings"][0]["code"]), ("roi_crop", "scope_roi_crop"))
        self.assertIn("roi_crop", b["explanations"][0]["text"])

    def test_busy_429_and_timeout_keeps_shared_slot(self):
        self.assertTrue(router_mod._BUSY.try_acquire())   # 넘기며 분석이 실행 중인 것과 같음 → 한 장도 429
        try:
            self.assertEqual(_post(self.client, [frame()], manifest(1)).status_code, 429)
        finally:
            router_mod._BUSY.release()
        slow = FakeSingle(delay=0.5)
        import httpx

        async def scenario():
            app = FastAPI()
            app.include_router(router_mod.router, prefix="/api/v1")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                f = lambda: [("frames", ("f000.png", frame(), "image/png"))]
                r1 = await c.post("/api/v1/knee/screen-analysis", files=f(), data={"manifest": manifest(1)})
                r2 = await c.post("/api/v1/knee/screen-analysis", files=[("frames", (f"f{i:03d}.png", frame(i), "image/png")) for i in range(10)],
                                  data={"manifest": manifest(10, intent="scroll")})
                for _ in range(100):
                    if not router_mod._BUSY.locked():
                        break
                    await asyncio.sleep(0.02)
                return r1.status_code, r2.status_code, router_mod._BUSY.locked()

        with mock.patch.object(single_mod, "acl_single", slow), \
                mock.patch.object(router_mod, "_setting", lambda k, d: "0.05" if k == "KNEE_SCREEN_ANALYSIS_TIMEOUT_S" else d):
            s1, s2, locked = asyncio.run(scenario())
        self.assertEqual((s1, s2, locked), (504, 429, False))   # 한 장 시간 초과 뒤에도 계산이 끝날 때까지 여러 장도 429
        self.assertEqual(self.vol.calls, [])


class LoaderTests(unittest.TestCase):
    """SINGLE01: 설정 폴더·고정 SHA·구조 검사. 잘못된 파일은 503(KneeModelNotReady), 경로·원 예외 비노출."""

    def _model(self, path):
        m = single_mod.AclSingleModel()
        p = mock.patch.object(single_mod.AclSingleModel, "bundle_dir", staticmethod(lambda: str(path)))
        p.start()
        self.addCleanup(p.stop)
        return m

    def test_empty_or_missing_dir_not_ready(self):
        for path in ("", Path(tempfile.mkdtemp()) / "none"):
            m = self._model(path)
            if path == "":
                self.assertFalse(m.configured())
            with self.assertRaises(errors.KneeModelNotReady) as cm:
                m.load()
            self.assertIsNone(cm.exception.__cause__)
            self.assertFalse(m.ready)

    @unittest.skipUnless(BUNDLE.is_dir(), "반입본 없음")
    def test_tampered_files_rejected(self):
        cases = {
            "model byte": lambda d: (d / "model_state.pt").write_bytes(_flip((d / "model_state.pt").read_bytes())),
            "preprocess": lambda d: (d / "preprocess.json").write_text(json.dumps({"resize": "256"}), encoding="utf-8"),
            "manifest": lambda d: (d / "model_manifest.json").write_text("{}", encoding="utf-8"),
            "missing report": lambda d: (d / "report.json").unlink(),
            "model as dir": lambda d: ((d / "model_state.pt").unlink(), (d / "model_state.pt").mkdir()),
        }
        for name, tamper in cases.items():
            d = Path(tempfile.mkdtemp()) / "b"
            shutil.copytree(BUNDLE, d)
            tamper(d)
            m = self._model(d)
            with self.assertRaises(errors.KneeModelNotReady, msg=name) as cm:
                m.load()
            self.assertNotIn(str(d), str(cm.exception))
            self.assertFalse(m.ready, name)
            shutil.rmtree(d.parent, ignore_errors=True)

    @unittest.skipUnless(BUNDLE.is_dir(), "반입본 없음")
    def test_real_bundle_loads_pinned_and_original_untouched(self):
        before = {p.name: p.stat().st_mtime_ns for p in BUNDLE.iterdir()}
        m = self._model(BUNDLE)
        m.load()
        self.assertEqual(m.model_sha256, "a34193b41fd0a902bd46fa47cc4dbed0e2a454f275e422474075727afd925199")
        self.assertTrue(m.model_id.startswith("knee_acl_single_v1@a34193b41fd0"))
        self.assertEqual(tuple(m.model.fc.weight.shape), (1, 512))
        self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in BUNDLE.iterdir()})   # 원본 9파일 그대로


@unittest.skipUnless(HAVE_REAL, "반입본 또는 fixture 없음")
class FixtureTests(unittest.TestCase):
    """SINGLE02: 고정 fixture 의 reference(동결 Colab 라이브러리, PC CPU FP32)와 서버 전처리·추론 대조, batch 독립, CAM=같은 logit."""

    @classmethod
    def setUpClass(cls):
        cls.doc = json.loads((FIXTURE / "fixture.json").read_text(encoding="utf-8"))
        cls.m = single_mod.AclSingleModel()
        cls.p = mock.patch.object(single_mod.AclSingleModel, "bundle_dir", staticmethod(lambda: str(BUNDLE)))
        cls.p.start()
        cls.m.load()
        decoded = [_gray(FIXTURE / it["file"]) for it in cls.doc["items"]]
        cls.grays = [g for g, _ in decoded]
        cls.rejected = {it["name"]: code for it, (_, code) in zip(cls.doc["items"], decoded) if code}

    @classmethod
    def tearDownClass(cls):
        cls.p.stop()

    def test_fixture_bound_and_scores_match_reference(self):
        import hashlib
        self.assertEqual(self.rejected, {"syn_constant_128": "blank_image"})   # 빈 영상만 서버 입력 계약(422)에서 거절
        self.assertEqual(self.doc["model_state_sha256"], self.m.model_sha256)
        tol = self.doc["tolerance"]
        for it, g in zip(self.doc["items"], self.grays):
            self.assertEqual(hashlib.sha256((FIXTURE / it["file"]).read_bytes()).hexdigest(), it["file_sha256"], it["name"])
            self.assertEqual(hashlib.sha256(np.ascontiguousarray(g).tobytes()).hexdigest(), it["pixel_sha256"], it["name"])   # 서버 디코드 = 학습 회색 변환
            out =self.m.analyze([g], include_overlay=False)
            self.assertLessEqual(abs(out["logit"] - it["logit"]), tol["logit_abs"], it["name"])
            self.assertLessEqual(abs(out["raw_score"] - it["raw_score"]), tol["raw_abs"], it["name"])

    def test_preprocess_bytes_identical_to_reference(self):
        import hashlib
        same = [hashlib.sha256(self.m.preprocess(g)[0].numpy().astype("<f4").tobytes()).hexdigest() == it["preprocessed_sha256"]
                for it, g in zip(self.doc["items"], self.grays)]
        self.assertTrue(all(same), same)

    def test_batch_independence_and_order(self):
        alone = [self.m.analyze([g], include_overlay=False)["logit"] for g in self.grays]
        sized = [g for g in self.grays]
        together = self.m.logits(sized[:6])   # 같은 크기 아니어도 전처리 뒤 224 로 같아짐
        rev = self.m.logits(list(reversed(sized[:6])))[::-1]
        np.testing.assert_allclose(together, alone[:6], atol=1e-4)
        np.testing.assert_allclose(rev, together, atol=1e-4)

    def test_cam_uses_same_logit_and_handles_no_activation(self):
        for it, g in zip(self.doc["items"], self.grays):
            plain = self.m.analyze([g], include_overlay=False)
            cam = self.m.analyze([g], include_overlay=True)
            self.assertLessEqual(abs(cam["raw_score"] - plain["raw_score"]), self.doc["tolerance"]["cam_vs_score_raw_abs"], it["name"])
            self.assertEqual(cam["cam_index"], 0)
            self.assertEqual(cam["overlay"].size, (224, 224))
            self.assertIsInstance(cam["activated"], bool)
            self.assertTrue(np.isfinite(cam["raw_score"]))
        self.assertEqual([h for h in self.m.model.layer4._forward_hooks], [])   # hook 제거됨
        self.assertTrue(all(p.grad is None for p in self.m.model.parameters()))   # 기울기 남기지 않음

    def test_api_end_to_end_with_real_model(self):
        with mock.patch.object(single_mod, "acl_single", self.m):
            client = _client()
            it = self.doc["items"][0]
            r = _post(client, [(FIXTURE / it["file"]).read_bytes()], manifest(1))
            blank = _post(client, [(FIXTURE / "images" / "syn_constant_128.png").read_bytes()], manifest(1))
        self.assertEqual((blank.status_code, blank.json()["code"]), (422, "blank_image"))
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        self.assertEqual((b["model_kind"], b["model_sha256"]), ("single_2d", self.m.model_sha256))
        self.assertAlmostEqual(b["acl"]["raw_score"], round(it["raw_score"], 6), places=5)
        self.assertIn(b["visualization"]["status"], ("shown", "no_activation"))


if __name__ == "__main__":
    unittest.main()
