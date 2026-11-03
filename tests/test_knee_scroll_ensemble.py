"""
무릎 넘기며 분석 앙상블(B+v4) 시험 — MSN-20261102-004 (ENSEMBLE_RUNTIME_CONTRACT r1, AC-02~AC-08).
라우터만 올린 작은 앱 + 가짜 모델로 계약을 확인한다(DB·NAS·HF 없음). 실제 모델 동등성은 설정 저장소의 check_equivalence.py 가 따로 확인한다.
실행: python -m pytest tests/test_knee_scroll_ensemble.py
"""
import io
import json
import math
import os
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

E = import_module("class.knee.scroll_ensemble")
sa = import_module("class.knee.scroll_analysis")
router_mod = import_module("routers.kneeScreenAnalysis")
acl_mod = import_module("class.knee.acl_volume")
loc_mod = import_module("class.knee.screen_localization")


def png(arr):
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def frames(n, size=(128, 128)):
    out = []
    for i in range(n):
        a = np.full((size[1], size[0]), 40, np.uint8)
        a[10 + i: 30 + i, 20:60] = 200
        out.append(png(a))
    return out


def manifest(n, intent="scroll", scope="full_slice"):
    return json.dumps({"schema_version": 1, "plane": "sagittal", "image_scope": scope, "capture_intent": intent, "capture_profile_id": "capture_profile_v1",
                       "frames": [{"sequence": i, "file_index": i, "captured_at_ms": i * 300} for i in range(n)]})


FOUND = {"status": "found", "raw": [0.5, 0.45, 0.3, 0.3], "conf": 0.8}


class FakeB:
    model_id, model_sha256, device, crop, backbone, pool = "fake@b", E.MODEL_FILES["legacy"]["sha256"], "cpu", None, "resnet18", "max"

    def __init__(self, z=0.3):
        self.z, self.logit_calls, self.analyze_calls = z, [], 0

    ready = True

    def load(self):
        self.load_calls = getattr(self, "load_calls", 0) + 1

    def logit(self, grays):
        self.logit_calls.append(len(grays))
        return self.z

    def analyze(self, grays, include_overlay=True):
        self.analyze_calls += 1
        return {"raw_score": 0.873, "cam_index": None, "overlay": None, "activated": False}


class FakeV4:
    ready = True

    def __init__(self, z=-1.0):
        self.z, self.calls = z, []

    def load(self):
        pass

    def logit(self, crops):
        self.calls.append(len(crops))
        return self.z


class FakeDet:
    ready = True

    def __init__(self, plan=None, exc=None):
        self.plan, self.exc, self.seen = plan, exc, 0

    def ensure_raw_ready(self):
        if self.exc:
            raise self.exc

    def detect_raw(self, g):
        i = self.seen
        self.seen += 1
        if self.plan is None:
            return dict(FOUND)
        return dict(self.plan[i]) if isinstance(self.plan[i], dict) else {"status": self.plan[i]}

    def detect(self, g):   # 기존 B 위치 안내용(4자리)
        return {"x_center": 0.5, "y_center": 0.4, "width": 0.3, "height": 0.2, "confidence": 0.9}


class SelectionAndMathTests(unittest.TestCase):
    def test_selection_boundaries(self):
        u = [(i, np.zeros((8, 8), np.uint8)) for i in range(21)]
        self.assertIsNone(E.select_middle(u[:14]))
        self.assertEqual(E.select_middle(u[:15])[0], 0)
        for n, start in ((16, 1), (17, 1), (18, 2), (19, 2), (20, 3)):
            s, chosen = E.select_middle(u[:n])
            self.assertEqual((s, [q for q, _ in chosen]), (start, list(range(start, start + 15))), n)
        with self.assertRaises(ValueError):
            E.select_middle(u)

    def test_required_valid_is_3_for_15(self):
        self.assertEqual(E.required_valid(15), 3)
        self.assertEqual(E.required_valid(20), 4)

    def test_crop_geometry_matches_reference_formula(self):
        g = E.crop_geometry([0.5, 0.5, 0.2, 0.1], 320, 320)
        self.assertEqual((g["side"], g["left"], g["top"], g["src_rect"], g["ok"]), (96, 112, 112, [112, 112, 208, 208], True))
        g = E.crop_geometry([0.005, 0.25, 0.01, 0.5], 128, 128)   # 가는 상자가 왼쪽 위 모서리 → side 96, 겹침 48×80/96² ≈ .417 < .5 → 거절
        self.assertEqual((g["side"], g["left"], g["top"], g["src_rect"], g["ok"]), (96, -48, -16, [0, 0, 48, 80], False))
        self.assertTrue(E.crop_geometry([0.02, 0.5, 0.2, 0.2], 300, 300)["ok"])   # 겹침 51/90 ≈ .567 → 통과
        self.assertEqual(E.crop_geometry([0.5, 0.5, 0.001, 0.001], 100, 100)["side"], 16)   # 최소 16px

    def test_make_crop_zero_pads_outside(self):
        gray = np.full((50, 50), 200, np.uint8)
        geom = E.crop_geometry([0.1, 0.1, 0.4, 0.4], 50, 50)
        c = E.make_crop(gray, geom)
        self.assertEqual(c.shape, (geom["side"], geom["side"]))
        self.assertEqual(int(c[0, 0]), 0)
        self.assertEqual(int(c[-1, -1]), 200)

    def test_validate_raw_rejects_before_rounding(self):
        self.assertEqual(E.validate_raw([0.5, 0.5, 0.2, 0.2], 0.9)["status"], "found")
        for raw, conf in (([0.5, 0.5, 0.0, 0.2], 0.9), ([float("nan"), 0.5, 0.2, 0.2], 0.9), ([0.5, 0.5, 0.2, 0.2], 0.2),
                          ([0.95, 0.5, 0.2, 0.2], 0.9), ([0.5, 0.5, 0.2, float("inf")], 0.9), (["x", 0.5, 0.2, 0.2], 0.9)):
            self.assertEqual(E.validate_raw(raw, conf)["status"], "invalid_box", (raw, conf))
        self.assertEqual(E.validate_raw([0.10005, 0.5, 0.2, 0.2], 0.9)["status"], "found")   # 끝점 허용오차 1e-4 안

    def test_combine_fixed_formula_and_boundary(self):
        zl, zv = 1.2, -0.7
        m, s = E.combine(zl, zv)
        want_m = 0.5 * (zl - (math.log(0.89) - math.log1p(-0.89))) + 0.5 * (zv - (math.log(0.08) - math.log1p(-0.08)))
        self.assertEqual(m, want_m)
        self.assertEqual(s, E.stable_sigmoid(want_m))
        self.assertEqual(E.reference_position(E.TAU_E), "above")                # >= 경계, 허용오차 없음
        self.assertEqual(E.reference_position(math.nextafter(E.TAU_E, 0.0)), "below")
        for bad in ((None, 1.0), (1.0, None), (float("nan"), 1.0), (1.0, float("inf")), (True, 1.0), (1, 1.0)):
            self.assertIsNone(E.combine(*bad), bad)
        self.assertEqual(E.combine(1e300, 1e300)[1], 1.0)   # 유한 logit 의 포화는 허용
        self.assertEqual(E.combine(-1e300, -1e300)[1], 0.0)

    def test_series_crops_partial_and_insufficient(self):
        g = np.full((128, 128), 90, np.uint8)
        plan = ["not_found"] * 12 + [dict(FOUND)] * 3
        out = E.series_crops([g] * 15, FakeDet(plan).detect_raw)
        self.assertEqual((out["status"], out["n_valid"], out["required"]), ("ok", 3, 3))
        plan = ["not_found"] * 13 + [dict(FOUND)] * 2
        self.assertEqual(E.series_crops([g] * 15, FakeDet(plan).detect_raw)["status"], "insufficient")
        plan = [{"status": "invalid_box"}] + [dict(FOUND, raw=[0.005, 0.25, 0.01, 0.5])] + [dict(FOUND)] * 13
        recs = E.series_crops([g] * 15, FakeDet(plan).detect_raw)["recs"]
        self.assertEqual([r["status"] for r in recs[:3]], ["invalid_box", "crop_rejected", "found"])


class BundleManifestTests(unittest.TestCase):
    def _bundle(self, mutate=None, files=True):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        m = {"kind": E.BUNDLE_KIND, "runtime_profile_id": E.RUNTIME_PROFILE_ID, "spec_id": E.SPEC_ID, "alpha": 0.5, "tau_legacy": 0.89, "tau_v4": 0.08,
             "tau_e": 0.4798988972937373,
             "files": {r: {"name": f"{r}.pt", "bytes": v["bytes"], "sha256": v["sha256"]} for r, v in E.MODEL_FILES.items()},
             "sources": dict(E.SOURCES_FIXED),
             "built": {"tool": "scripts/knee_scroll_ensemble_bundle/build_bundle.py", "tool_sha256": "a" * 64, "python": "3.12.13",
                       "torch": "2.6.0+cu124", "ultralytics": "8.4.153"}}
        text = json.dumps(m)
        if mutate:
            text = mutate(m, text)
        Path(d, E.BUNDLE_MANIFEST).write_text(text, encoding="utf-8")
        return Path(d)

    def test_valid_manifest_reads(self):
        self.assertEqual(E.read_bundle_manifest(self._bundle())["spec_id"], E.SPEC_ID)

    def test_rejects(self):
        cases = {
            "dup_key": lambda m, t: t.replace('"alpha": 0.5', '"alpha": 0.5, "alpha": 0.5'),
            "nan": lambda m, t: t.replace('"alpha": 0.5', '"alpha": NaN'),
            "tau_changed": lambda m, t: json.dumps({**m, "tau_e": 0.5}),
            "tau_string_type": lambda m, t: t.replace('"tau_v4": 0.08', '"tau_v4": "0.08"'),
            "alpha_int_type": lambda m, t: t.replace('"alpha": 0.5', '"alpha": 1'),
            "sha_override": lambda m, t: json.dumps({**m, "files": {**m["files"], "v4": {**m["files"]["v4"], "sha256": "0" * 64}}}),
            "path_escape": lambda m, t: json.dumps({**m, "files": {**m["files"], "v4": {**m["files"]["v4"], "name": "../v4.pt"}}}),
            "extra_key": lambda m, t: json.dumps({**m, "model_path": "x"}),
            "zip_sha": lambda m, t: json.dumps({**m, "sources": {**m["sources"], "v4_model_zip_sha256": "1" * 64}}),
            # ENSEMBLE-LOCAL-002: 모든 멤버의 비유한 수(리터럴·지수 overflow, 최상위·중첩·부가 필드)
            "nested_infinity": lambda m, t: t.replace('"python": "3.12.13"', '"python": "3.12.13", "probe": Infinity'),
            "nested_neg_infinity": lambda m, t: t.replace('"v4_zip_member"', '"probe": -Infinity, "v4_zip_member"'),
            "built_1e999": lambda m, t: t.replace('"built": {', '"built": {"probe": 1e999, ', 1),
            "sources_neg_1e999": lambda m, t: t.replace('"sources": {', '"sources": {"probe": -1e999, ', 1),
            "top_1e999": lambda m, t: t.replace('"alpha": 0.5', '"alpha": 1e999'),
            "files_bytes_1e999": lambda m, t: t.replace('"bytes": %d' % E.MODEL_FILES["v4"]["bytes"], '"bytes": 1e999'),
            # 필수 출처·버전 누락·변조·형식
            "sidecar_missing": lambda m, t: json.dumps({**m, "sources": {k: v for k, v in m["sources"].items() if k != "v4_selection_sha256"}}),
            "sidecar_changed": lambda m, t: json.dumps({**m, "sources": {**m["sources"], "v4_run_protocol_sha256": "2" * 64}}),
            "sidecar_extra": lambda m, t: json.dumps({**m, "sources": {**m["sources"], "note": "x"}}),
            "built_empty": lambda m, t: json.dumps({**m, "built": {}}),
            "built_missing_torch": lambda m, t: json.dumps({**m, "built": {k: v for k, v in m["built"].items() if k != "torch"}}),
            "built_wrong_type": lambda m, t: json.dumps({**m, "built": {**m["built"], "python": 3.12}}),
            "built_bad_tool_sha": lambda m, t: json.dumps({**m, "built": {**m["built"], "tool_sha256": "xyz"}}),
        }
        for name, fn in cases.items():
            with self.assertRaises(E.BundleError, msg=name):
                E.read_bundle_manifest(self._bundle(fn))

    def test_file_fingerprint_mismatch_is_closed(self):
        root = self._bundle()
        Path(root, "v4.pt").write_bytes(b"not the model")
        with mock.patch.dict(os.environ, {E.BUNDLE_SETTING: str(root)}):
            with self.assertRaises(E.BundleError) as cm:
                E.read_bundle_bytes("v4")
        self.assertEqual(cm.exception.reason, "bundle_file_mismatch")

    def test_v4_load_from_bad_bundle_is_unavailable_not_b(self):
        root = self._bundle()
        with mock.patch.dict(os.environ, {E.BUNDLE_SETTING: str(root)}):
            v = E.V4Model()
            with self.assertRaises(E.EnsembleUnavailable):
                v.load()


class PreflightNoBundleTests(unittest.TestCase):
    def test_preflight_without_bundle_never_calls_hf_download(self):   # ENSEMBLE-LOCAL-001 (실제 B 로더, 다운로드는 감시만)
        svc = import_module("class.knee.ensemble").KneeEnsembleService
        calls = []
        fresh_b = acl_mod.AclVolumeModel()
        env = {k: v for k, v in os.environ.items() if k != E.BUNDLE_SETTING}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(svc, "_download", lambda f: calls.append(f)), \
                mock.patch.object(acl_mod, "acl_volume", fresh_b), mock.patch.object(E, "v4_model", E.V4Model()):
            with self.assertRaises(E.EnsembleUnavailable) as cm:
                E.preflight()
        self.assertEqual((cm.exception.reason, calls, fresh_b.ready), ("bundle_not_configured", [], False))


class ApiTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"KNEE_SCREEN_LOCALIZATION_ENABLED": "1", E.ENABLED_SETTING: "1"})
        env.start()
        self.addCleanup(env.stop)
        app = FastAPI()
        app.include_router(router_mod.router, prefix="/api/v1")
        self.client = TestClient(app)
        self.b, self.v4, self.det = FakeB(), FakeV4(), FakeDet()
        self.manifest_reads = []
        for target, attr, val in ((acl_mod, "acl_volume", self.b), (E, "v4_model", self.v4), (loc_mod, "locator", self.det),
                                  (E, "bundle_dir", lambda: Path("fake_bundle")),
                                  (E, "read_bundle_manifest", lambda root: self.manifest_reads.append(root) or {})):
            p = mock.patch.object(target, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def post(self, n=15, mode="ensemble", intent="scroll", scope="full_slice", extra=None, blobs=None):
        blobs = blobs or frames(n)
        files = [("frames", (f"f{i:03d}.png", b, "image/png")) for i, b in enumerate(blobs)]
        data = {"manifest": manifest(len(blobs), intent, scope)}
        if mode is not None:
            data["scroll_score_mode"] = mode
        data.update(extra or {})
        return self.client.post("/api/v1/knee/screen-analysis", files=files, data=data)

    def test_ensemble_evaluated_contract(self):
        r = self.post(15)
        self.assertEqual(r.status_code, 200, r.text)
        b = r.json()
        m, s = E.combine(0.3, -1.0)
        self.assertEqual((b["score_mode"], b["runtime_profile_id"], b["analysis_status"], b["model_kind"], b["model_sha256"]),
                         ("ensemble", E.RUNTIME_PROFILE_ID, "evaluated", "volume", None))
        self.assertEqual(b["acl"]["raw_score"], s)                  # 반올림하지 않은 decision_score
        self.assertEqual(b["acl"]["display_score"], s)
        self.assertEqual((b["acl"]["score_kind"], b["acl"]["decision"], b["acl"]["calibrated_score"]), ("ensemble_decision_score", "score_only", None))
        ref = b["acl"]["reference"]
        self.assertEqual((ref["threshold"], ref["position"], ref["input_condition"]), (E.TAU_E, "above" if s >= E.TAU_E else "below", "unverified_screen_capture"))
        self.assertIn("47.99점", ref["text"])
        self.assertNotIn("%", b["acl"]["display_text"])
        self.assertEqual((b["ensemble"]["status"], b["ensemble"]["n_valid"], b["ensemble"]["required"], b["ensemble"]["input_condition_verified"]), ("ok", 15, 3, False))
        self.assertEqual(set(b["ensemble"]["models"]), {"legacy", "v4", "yolo"})
        self.assertEqual((b["capture"]["used_count"], b["capture"]["used_sequences"]), (15, list(range(15))))
        self.assertEqual((self.b.logit_calls, self.v4.calls, self.b.analyze_calls), ([15], [15], 0))
        v = b["visualization"]
        self.assertEqual((v["status"], v["kind"], v["cam"], v["overlay_frame_sequence"]), ("shown", "input_crop_location", "not_computed", 7))
        self.assertEqual(self.det.seen, 15)                          # 그림 때문에 YOLO 를 더 부르지 않음
        text = json.dumps(b, ensure_ascii=False)
        for must in ("손상 확률이 아니", "0을 포함", "39건에서 118건", "정확도는 확인하지 않았"):
            self.assertIn(must, text)

    def test_20_frames_uses_middle_15(self):
        b = self.post(20).json()
        self.assertEqual((b["capture"]["used_sequences"], b["capture"]["selection"]["start"]), (list(range(3, 18)), 3))
        self.assertEqual(self.b.logit_calls, [15])
        self.assertTrue(any(w["code"] == "ensemble_middle_15" for w in b["warnings"]))

    def test_10_to_14_not_evaluated_without_models(self):
        for n in (10, 14):
            r = self.post(n)
            self.assertEqual(r.status_code, 200, r.text)
            b = r.json()
            self.assertEqual((b["analysis_status"], b["acl"]["raw_score"], b["acl"]["display_score"], b["acl"]["reference"], b["acl"]["decision"]),
                             ("not_evaluated", None, None, None, "not_evaluated"))
            self.assertEqual((b["ensemble"]["status"], b["ensemble"]["reason"]), ("unavailable", "insufficient_ensemble_frames"))
            self.assertIn("15장", b["acl"]["not_evaluated_text"])
        self.assertEqual((self.b.logit_calls, self.b.analyze_calls, self.v4.calls, self.det.seen), ([], 0, [], 0))   # B 로 몰래 대신하지 않음

    def test_insufficient_crops_not_evaluated_and_no_b_score(self):
        self.det.plan = ["not_found"] * 13 + [dict(FOUND)] * 2
        b = self.post(15).json()
        self.assertEqual((b["analysis_status"], b["acl"]["raw_score"], b["acl"]["reference"]), ("not_evaluated", None, None))
        self.assertEqual((b["ensemble"]["reason"], b["ensemble"]["n_valid"], b["ensemble"]["required"]), ("insufficient_valid_crops", 2, 3))
        self.assertIn("2/15", b["acl"]["not_evaluated_text"])
        self.assertEqual((b["visualization"]["kind"], b["visualization"]["overlay_png_base64"]), ("none", None))
        self.assertEqual(self.v4.calls, [])
        self.assertEqual(b["explanations"], [])

    def test_middle_frame_missing_hides_picture_but_scores(self):
        plan = [dict(FOUND)] * 15
        plan[7] = "not_found"
        self.det.plan = plan
        b = self.post(15).json()
        self.assertEqual(b["analysis_status"], "evaluated")
        self.assertEqual((b["visualization"]["status"], b["visualization"]["kind"], b["visualization"]["overlay_png_base64"]), ("withheld", "none", None))
        self.assertEqual(self.det.seen, 15)

    def test_disabled_server_rejects_ensemble_503(self):
        with mock.patch.dict(os.environ, {E.ENABLED_SETTING: "0"}):
            r = self.post(15)
        self.assertEqual((r.status_code, r.json()["code"], r.json()["score_mode"]), (503, "ensemble_disabled", "ensemble"))
        self.assertNotIn("acl", r.json())
        self.assertEqual(self.b.logit_calls + [self.b.analyze_calls], [0])

    def test_invalid_and_duplicate_mode_400(self):
        self.assertEqual(self.post(15, mode="ENSEMBLE").status_code, 400)
        r = self.client.post("/api/v1/knee/screen-analysis", files=[("frames", ("a.png", frames(1)[0], "image/png"))],
                             data={"manifest": manifest(1), "scroll_score_mode": ["ensemble", "legacy"]})
        self.assertEqual((r.status_code, r.json()["code"]), (400, "invalid_score_mode"))

    def test_single_and_roi_crop_rejected_422(self):
        r = self.post(1, intent="single")
        self.assertEqual((r.status_code, r.json()["code"]), (422, "unsupported_ensemble_input"))
        r = self.post(15, scope="roi_crop")
        self.assertEqual((r.status_code, r.json()["code"]), (422, "unsupported_ensemble_input"))

    def test_model_unavailable_503_no_score(self):
        self.det.exc = loc_mod.LocationUnavailable("model_mismatch")
        r = self.post(15)
        self.assertEqual((r.status_code, r.json()["code"], r.json()["score_mode"]), (503, "ensemble_unavailable", "ensemble"))
        self.assertNotIn("model_mismatch", r.text)                   # 내부 이유는 로그에만
        self.assertEqual(self.b.analyze_calls, 0)

    def test_b_hash_mismatch_unavailable(self):
        self.b.model_sha256 = "f" * 64
        self.assertEqual(self.post(15).status_code, 503)

    def test_nonfinite_logit_is_503_not_score(self):
        self.v4.z = float("nan")
        r = self.post(15)
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("raw_score", r.text)

    def test_busy_429_keeps_slot(self):
        self.assertTrue(router_mod._BUSY.try_acquire())
        try:
            r = self.post(15)
            self.assertEqual((r.status_code, r.json()["code"], r.json()["score_mode"]), (429, "busy", "ensemble"))
            r = self.post(15, mode=None)                              # 기존 B 요청의 오류에도 모드를 함께 준다
            self.assertEqual((r.status_code, r.json()["code"], r.json()["score_mode"]), (429, "busy", "legacy"))
        finally:
            router_mod._BUSY.release()

    def test_no_bundle_ensemble_503_before_any_model(self):   # ENSEMBLE-LOCAL-001
        with mock.patch.object(E, "bundle_dir", lambda: None):
            for n in (15, 12):
                r = self.post(n)
                self.assertEqual((r.status_code, r.json()["code"], r.json()["score_mode"]), (503, "ensemble_unavailable", "ensemble"))
                self.assertNotIn("acl", r.json())
        self.assertEqual((getattr(self.b, "load_calls", 0), self.b.logit_calls, self.v4.calls, self.det.seen), (0, [], [], 0))
        with mock.patch.object(E, "bundle_dir", lambda: None):   # 기존 B 는 묶음 없이도 그대로(기본 HF 경로 유지)
            r = self.post(15, mode=None)
        self.assertEqual((r.status_code, r.json()["score_mode"]), (200, "legacy"))

    def test_invalid_manifest_ensemble_503(self):
        def bad(root):
            raise E.BundleError("bundle_manifest_invalid")
        with mock.patch.object(E, "read_bundle_manifest", bad):
            r = self.post(15)
        self.assertEqual((r.status_code, r.json()["code"]), (503, "ensemble_unavailable"))
        self.assertEqual((self.b.logit_calls, self.v4.calls, self.det.seen), ([], [], 0))

    def test_first_load_checks_all_files_before_models(self):
        self.v4.ready = False                                       # 아직 안 읽은 모델이 있으면 세 파일 지문부터
        calls = []

        def files_bad():
            calls.append(1)
            raise E.EnsembleUnavailable("bundle_file_mismatch")
        with mock.patch.object(E, "check_bundle_files", files_bad):
            r = self.post(15)
        self.assertEqual((r.status_code, calls, getattr(self.b, "load_calls", 0), self.det.seen), (503, [1], 0, 0))

    def test_explanation_states_threshold_provenance(self):  # ENSEMBLE-LOCAL-003
        b = self.post(15).json()
        text = json.dumps(b, ensure_ascii=False)
        for must in ("식과 비중은 사전에 고정", "참고선은 보정 자료 181건에서 정했", "시험 자료 181건은 이미 여러 번 사용한 자료로, 고정 뒤 탐색적으로 감사"):
            self.assertIn(must, b["explanations"][0]["text"])
            self.assertIn(must, text)
        self.assertNotIn("탐색적 비교에서 정", text)
        self.assertIn("calibration181", b["evidence"]["source"])

    def test_config_endpoint_reports_default_mode(self):   # 화면 기본값(앙상블) 결정용, 모델은 읽지 않음
        b = self.client.get("/api/v1/knee/screen-analysis/config").json()
        self.assertEqual(b, {"ensemble_enabled": True, "ensemble_ready": True, "default_scroll_score_mode": "ensemble"})
        with mock.patch.object(E, "bundle_dir", lambda: None):
            b = self.client.get("/api/v1/knee/screen-analysis/config").json()
        self.assertEqual((b["ensemble_ready"], b["default_scroll_score_mode"]), (False, "legacy"))
        with mock.patch.dict(os.environ, {E.ENABLED_SETTING: "0"}):
            b = self.client.get("/api/v1/knee/screen-analysis/config").json()
        self.assertEqual((b["ensemble_enabled"], b["ensemble_ready"], b["default_scroll_score_mode"]), (False, False, "legacy"))
        self.assertEqual((getattr(self.b, "load_calls", 0), self.b.logit_calls, self.v4.calls, self.det.seen), (0, [], [], 0))
        self.assertNotIn("fake_bundle", json.dumps(b))

    def test_legacy_default_unchanged_plus_additive_fields(self):
        for mode in (None, "legacy"):
            self.b.logit_calls = []
            r = self.post(15, mode=mode)
            self.assertEqual(r.status_code, 200, r.text)
            b = r.json()
            self.assertEqual((b["score_mode"], b["runtime_profile_id"], b["ensemble"]), ("legacy", None, None))
            self.assertEqual((b["acl"]["raw_score"], b["acl"]["score_kind"], b["model_sha256"]), (0.873, "raw", self.b.model_sha256))
            self.assertEqual(self.b.logit_calls, [])
            self.assertEqual(self.v4.calls, [])
        r = self.post(12, mode="legacy")                             # B 는 기존대로 10~14장 허용
        self.assertEqual((r.status_code, r.json()["capture"]["used_count"]), (200, 12))


if __name__ == "__main__":
    unittest.main()
