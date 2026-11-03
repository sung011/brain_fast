"""
무릎 화면 분석 보정·비교 기준선 sidecar 시험 — MSN-20261021-001 CALIBRATION_PRIORITY_DESIGN A/B·UI01.
sidecar 가 없거나·지문/모델/범위가 다르면 raw 만(score_only) + 사유, 맞으면 서버가 고른 비교선·참고구간. 0.5 방향 문구 없음.
실제 sidecar 파일은 이 PC 의 설계 폴더(calibration_priority_v2)에 있을 때만 쓰고, 없으면 해당 시험은 건너뛴다.
실행: python -m pytest tests/test_knee_score_reference.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ref_mod = import_module("class.knee.score_reference")
sa = import_module("class.knee.scroll_analysis")
router_mod = import_module("routers.kneeScreenAnalysis")
single_mod = import_module("class.knee.single_image")
acl_mod = import_module("class.knee.acl_volume")

SIDE = Path(r"C:\Users\user\Desktop\setting\designs\MSN-20261021-001\calibration_priority_v2")
SINGLE_FILE, VOLUME_FILE = SIDE / "sidecar_single_v2.json", SIDE / "sidecar_volume_v15.json"
HAVE = SINGLE_FILE.exists() and VOLUME_FILE.exists()
SINGLE_SHA = ref_mod.PINNED["single"]["model_sha256"]
VOLUME_SHA = ref_mod.PINNED["volume"]["model_sha256"]


def _settings(single=None, volume=None):
    vals = {"KNEE_SINGLE_CALIBRATION_FILE": str(single) if single else "", "KNEE_VOLUME_REFERENCE_FILE": str(volume) if volume else ""}
    return mock.patch.object(ref_mod, "_setting", lambda name: vals.get(name, ""))


class FakeModel:
    def __init__(self, sha, raw):
        self.model_id, self.model_sha256, self.device, self.raw = "fake", sha, "cpu", raw

    def configured(self):
        return True

    def analyze(self, grays, include_overlay=True):
        import math
        return {"raw_score": self.raw, "logit": math.log(self.raw / (1 - self.raw)), "cam_index": 0, "overlay": Image.new("RGB", (224, 224)), "activated": True}


def _png(k=0):
    import io
    import numpy as np
    a = np.full((128, 128), 40, np.uint8)
    a[10 + k: 30 + k, 20:60] = 200
    b = io.BytesIO()
    Image.fromarray(a).save(b, "PNG")
    return b.getvalue()


def _post(blobs, intent, scope="full_slice"):
    app = FastAPI()
    app.include_router(router_mod.router, prefix="/api/v1")
    man = {"schema_version": 1, "plane": "sagittal", "image_scope": scope, "capture_intent": intent, "capture_profile_id": "capture_profile_v3",
           "frames": [{"sequence": i, "file_index": i, "captured_at_ms": i * 300} for i in range(len(blobs))]}
    files = [("frames", (f"f{i:03d}.png", b, "image/png")) for i, b in enumerate(blobs)]
    return TestClient(app).post("/api/v1/knee/screen-analysis", files=files, data={"manifest": json.dumps(man)})


class NotConfiguredTests(unittest.TestCase):
    def test_no_sidecar_is_raw_only_with_reason_and_no_half_threshold_text(self):
        with _settings(), mock.patch.object(single_mod, "acl_single", FakeModel(SINGLE_SHA, 0.62)):
            b = _post([_png()], "single").json()
        a = b["acl"]
        self.assertEqual((a["decision"], a["score_kind"], a["reference"], a["reference_status"], a["band"], a["calibrated_score"]),
                         ("score_only", "raw", None, "not_configured", None, None))
        text = b["explanations"][0]["text"]
        self.assertNotIn("손상 라벨과 유사한 패턴이 높게", text)
        self.assertIn("의료 진단이 아닙니다", text)
        self.assertIsNone(b["evidence"]["calibration_profile"])


@unittest.skipUnless(HAVE, "sidecar 없음")
class SingleReferenceTests(unittest.TestCase):
    def run_single(self, raw, scope="full_slice", sha=SINGLE_SHA, single=SINGLE_FILE):
        with _settings(single=single), mock.patch.object(single_mod, "acl_single", FakeModel(sha, raw)):
            return _post([_png()], "single", scope).json()

    def test_reference_and_band_from_server_sidecar(self):
        doc = json.loads(SINGLE_FILE.read_text(encoding="utf-8"))
        tau, (l, h) = doc["reference_threshold_raw"], doc["band_raw"]
        b = self.run_single(0.724)
        a = b["acl"]
        self.assertEqual(a["decision"], "reference_compared")
        self.assertEqual((a["reference"]["threshold"], a["reference"]["position"], a["band"]), (tau, "above", "high"))
        self.assertEqual(a["reference"]["input_condition"], "assumed_dataset_mid_slice")
        self.assertEqual((a["score_kind"], a["calibrated_score"], a["display_score"]), ("raw", None, 0.724))   # 감사에서 보정 표시 미채택
        ev = b["evidence"]["calibration_profile"]
        self.assertEqual((ev["legacy_test_reuse"], ev["fresh_test"], ev["independent_test"], ev["calibrated_display_adopted"]), (True, False, False, False))
        t = b["explanations"][0]["text"]
        self.assertIn("비교선", t)
        self.assertIn("정상'이라는 뜻이 아님", t)
        self.assertIn("의료 진단이 아닙니다", t)
        self.assertNotIn("손상 라벨과 유사한 패턴이", t)
        lo = self.run_single(0.05)["acl"]
        self.assertEqual((lo["reference"]["position"], lo["band"]), ("below", "low"))
        self.assertIn("의료 진단", self.run_single(0.05)["explanations"][0]["text"])
        # 경계: raw == τ 는 위(>=), l·h 경계는 판단 어려움 구간
        self.assertEqual(self.run_single(tau)["acl"]["reference"]["position"], "above")
        self.assertEqual(self.run_single(l)["acl"]["band"], "uncertain")
        self.assertEqual(self.run_single(h)["acl"]["band"], "uncertain")

    def test_out_of_scope_and_mismatch_fall_back_to_raw(self):
        self.assertEqual(self.run_single(0.7, scope="roi_crop")["acl"]["reference_status"], "scope_not_supported")
        self.assertEqual(self.run_single(0.7, sha="1" * 64)["acl"]["reference_status"], "model_mismatch")
        d = Path(tempfile.mkdtemp())
        tampered = d / "s.json"
        doc = json.loads(SINGLE_FILE.read_text(encoding="utf-8"))
        doc["reference_threshold_raw"] = 0.5
        tampered.write_text(json.dumps(doc), encoding="utf-8")
        a = self.run_single(0.7, single=tampered)["acl"]
        self.assertEqual((a["decision"], a["reference"], a["reference_status"]), ("score_only", None, "sidecar_sha_mismatch"))
        a = self.run_single(0.7, single=d / "none.json")["acl"]
        self.assertEqual(a["reference_status"], "sidecar_missing")
        shutil.rmtree(d, ignore_errors=True)

    def test_validation_rules(self):
        doc = json.loads(SINGLE_FILE.read_text(encoding="utf-8"))
        bad = {**doc, "calibration": {"adopted": True, "a": -1.0, "b": 0.0}}
        self.assertEqual(ref_mod._validate("single", bad), "sidecar_invalid")
        bad = {**doc, "band_raw": [0.3, 0.7]}   # τ 가 band 밖
        self.assertEqual(ref_mod._validate("single", bad), "sidecar_invalid")
        bad = {**doc, "input_policy": "roi_mid"}
        self.assertEqual(ref_mod._validate("single", bad), "sidecar_mismatch")
        self.assertIsNone(ref_mod._validate("single", doc))


@unittest.skipUnless(HAVE, "sidecar 없음")
class VolumeReferenceTests(unittest.TestCase):
    def run_scroll(self, n, raw, scope="full_slice"):
        with _settings(volume=VOLUME_FILE), mock.patch.object(acl_mod, "acl_volume", FakeModel(VOLUME_SHA, raw)), \
             mock.patch.dict(os.environ, {"KNEE_SCREEN_LOCALIZATION_ENABLED": "1"}):
            return _post([_png(k) for k in range(n)], "scroll", scope).json()

    def test_screen_share_15_frames_omit_reference_but_keep_score_cam_explanation(self):
        """CALIB-REVIEW-003: 화면 공유의 15장은 가운데 연속 15장인지 서버가 모름 → 참고선만 생략, 점수·해설 유지.
        CAM_LOCALIZATION_STEP2 이후 넘기며 분석 그림은 볼륨 CAM 이 아니라 별도 ACL 위치 박스(찾으면 shown, 못 찾으면 위치만 withheld)."""
        for n, scope in ((15, "full_slice"), (15, "roi_crop"), (10, "full_slice"), (20, "full_slice")):
            b = self.run_scroll(n, 0.95, scope=scope)
            a = b["acl"]
            self.assertEqual((a["reference"], a["reference_status"]), (None, "condition_unverified"), (n, scope))
            self.assertEqual((a["raw_score"], a["display_score"], a["score_kind"], a["decision"]), (0.95, 0.95, "raw", "score_only"))
            v = b["visualization"]
            self.assertIn((v["status"], v["kind"]), (("shown", "anatomical_box"), ("withheld", "none")), (n, scope))
            self.assertNotEqual(v["kind"], "grad_cam")
            if scope == "roi_crop":
                self.assertEqual((v["status"], v["localization"]["status"]), ("withheld", "unsupported_scope"))
            self.assertIn("참고선 없이 점수만", b["explanations"][0]["text"])
            self.assertNotIn("손상 라벨과 유사한 패턴이", b["explanations"][0]["text"])
            self.assertIsNone(b["evidence"]["reference_profile"])

    def test_reference_only_when_server_verified_center_15(self):
        tau = json.loads(VOLUME_FILE.read_text(encoding="utf-8"))["reference_threshold_raw"]
        with _settings(volume=VOLUME_FILE):
            ok = ref_mod.volume_reference(model_sha256=VOLUME_SHA, scope="full_slice", used_count=15, raw=0.95, condition_verified=True)
            self.assertEqual((ok["applied"], ok["threshold_raw"], ok["position"]), (True, tau, "above"))
            self.assertEqual(ref_mod.volume_reference(model_sha256=VOLUME_SHA, scope="full_slice", used_count=15, raw=0.3, condition_verified=True)["position"], "below")
            for kw in ({"used_count": 10}, {"used_count": 20}, {"scope": "roi_crop"}):
                args = {**dict(model_sha256=VOLUME_SHA, scope="full_slice", used_count=15, raw=0.95, condition_verified=True), **kw}
                self.assertEqual(ref_mod.volume_reference(**args)["reason"], "condition_not_supported", kw)
            for truthy in ("true", 1, "yes"):   # 참/거짓 이외(클라이언트가 보낸 듯한 값)는 확인으로 치지 않음
                self.assertEqual(ref_mod.volume_reference(model_sha256=VOLUME_SHA, scope="full_slice", used_count=15, raw=0.95,
                                                          condition_verified=truthy)["reason"], "condition_unverified")


if __name__ == "__main__":
    unittest.main()
