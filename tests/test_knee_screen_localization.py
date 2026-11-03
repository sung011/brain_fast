"""
넘기며 분석 ACL 위치 안내(class/knee/screen_localization.py) 시험 — MSN-20261021-001 CAM_LOCALIZATION_STEP2 AC03~AC05·AC10.
실제 YOLO 시험은 이 PC 의 HF 캐시(acl_yolo.pt)와 로컬 KneeMRI PNG 가 있을 때만 돈다(내려받기 없음).
실행: python -m pytest tests/test_knee_screen_localization.py
"""
import base64
import io
import json
import os
import sys
import threading
import time
import unittest
from importlib import import_module
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
loc = import_module("class.knee.screen_localization")

DATA = Path(r"C:\Users\user\Desktop\메디스캔노트(깃허브)\knee_series_data")
MANIFEST = DATA / "knee_series" / "cases_manifest_case-002_case-908.json"


def decode(b64):
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB"))


def drawn_bbox(img):
    m = (img == loc.BOX_COLOR).all(axis=-1)
    ys, xs = np.where(m)
    return (xs.min(), ys.min(), xs.max(), ys.max()) if len(xs) else None


class BoxTests(unittest.TestCase):
    def test_to_box_validation(self):
        self.assertEqual(loc.to_box({"x_center": .5, "y_center": .4, "width": .3, "height": .2}), {"x": .35, "y": .3, "width": .3, "height": .2})
        for bad in ({"x_center": float("nan"), "y_center": .5, "width": .2, "height": .2}, {"x_center": .5, "y_center": .5, "width": float("inf"), "height": .2},
                    {"x_center": .5, "y_center": .5, "width": 0, "height": .2}, {"x_center": .5, "y_center": .5, "width": .2, "height": -.1},
                    {"x_center": .95, "y_center": .5, "width": .2, "height": .2}, {"x_center": .05, "y_center": .5, "width": .2, "height": .2},
                    {"x_center": .5}, {"x_center": "a", "y_center": .5, "width": .2, "height": .2}, None):
            self.assertIsNone(loc.to_box(bad), bad)
        # 4자리 반올림 끝점 오차(≤1e-4)만 보정
        b = loc.to_box({"x_center": .10005, "y_center": .5, "width": .2002, "height": .2})
        self.assertEqual(b["x"], 0.0)
        self.assertIsNone(loc.to_box({"x_center": .1, "y_center": .5, "width": .2006, "height": .2}))   # 3e-4 넘침은 거절

    def test_png_box_position_on_non_square_and_edges(self):
        # 비대칭 비정사각 입력: 왼쪽 위가 밝음(뒤집기·회전이 있으면 위치가 어긋남)
        for (w, h) in ((300, 200), (200, 700), (1024, 600), (64, 64)):
            g = np.full((h, w), 30, np.uint8); g[: h // 3, : w // 4] = 220
            for box in ({"x": .1, "y": .2, "width": .3, "height": .25}, {"x": 0.0, "y": 0.0, "width": .2, "height": .2},
                        {"x": .8, "y": .75, "width": .2, "height": .25}, {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}):
                b64, pw, ph = loc.render_png(g, box)
                img = decode(b64)
                s = min(1.0, 512 / max(w, h))
                self.assertEqual((pw, ph), (round(w * s), round(h * s)))
                self.assertEqual(img.shape[:2], (ph, pw))
                x0, y0, x1, y1 = drawn_bbox(img)
                ex0, ey0 = box["x"] * pw, box["y"] * ph
                ex1, ey1 = (box["x"] + box["width"]) * pw - 1, (box["y"] + box["height"]) * ph - 1
                for got, exp in ((x0, ex0), (y0, ey0), (x1, ex1), (y1, ey1)):
                    self.assertLessEqual(abs(got - exp), 1.5, (w, h, box))
                # 테두리만: 박스 안쪽 가운데 화소는 원 영상 회색 그대로(채움·열지도 없음)
                cx, cy = int((ex0 + ex1) / 2), int((ey0 + ey1) / 2)
                self.assertEqual(len(set(img[cy, cx].tolist())), 1)
                # 밝은 왼쪽 위 영역이 그림에서도 왼쪽 위(뒤집기·회전 없음)
                self.assertGreater(img[2, 2, 0], 150) if box["x"] > 0.05 else None


class LocateMiddleTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"KNEE_SCREEN_LOCALIZATION_ENABLED": "1"})
        p.start()
        self.addCleanup(p.stop)

    def frames(self, n):
        return [(i * 2, np.full((50, 60), i, np.uint8)) for i in range(n)]

    def test_middle_selection_and_status(self):
        seen = []

        def det(g):
            seen.append(int(np.asarray(g)[0, 0]))
            return {"x_center": .5, "y_center": .5, "width": .2, "height": .2, "confidence": .9}
        with mock.patch.object(loc.locator, "detect", det):
            for n, j in ((10, 5), (15, 7), (20, 10), (11, 5)):
                r = loc.locate_middle(self.frames(n), "full_slice", True)
                self.assertEqual((r["status"], r["frame_ordinal"], r["frame_sequence"]), ("shown", j + 1, j * 2))
                self.assertEqual(seen[-1], j)
            self.assertEqual(loc.locate_middle(self.frames(15), "full_slice", False), {"status": "not_requested"})
            self.assertEqual(loc.locate_middle(self.frames(15), "roi_crop", True)["status"], "unsupported_scope")
        self.assertEqual(len(seen), 4)                                              # 미요청·roi_crop 은 호출 0


class LocatorTests(unittest.TestCase):
    def test_raw_prediction_is_rejected_before_clamping(self):
        class Boxes:
            conf = np.array([.9])
            def __init__(self, values):
                self.xywhn = np.asarray([values], dtype=float)
            def __len__(self):
                return 1

        class Model:
            def __init__(self, values):
                self.values = values
            def predict(self, *a, **k):
                return [type("Result", (), {"boxes": Boxes(self.values)})()]

        frames = [(i * 2, np.zeros((40, 60), np.uint8)) for i in range(15)]
        bad = ([.5, .5, float("inf"), .2], [.5, .5, 2, .2],
               [.5, .5, .2, float("inf")], [.5, .5, .2, 2],
               [float("nan"), .5, .2, .2], [.5, .5, -.2, .2],
               [.95, .5, .2, .2], [.5, .5, 0, .2])
        with mock.patch.dict(os.environ, {"KNEE_SCREEN_LOCALIZATION_ENABLED": "1"}):
            for values in bad:
                with self.subTest(values=values):
                    detector = loc._Locator()
                    detector._model = Model(values)
                    with mock.patch.object(loc, "locator", detector):
                        out = loc.locate_middle(frames, "full_slice", True)
                    self.assertEqual(out["status"], "invalid_box")
                    self.assertIsNone(out["box"])
                    self.assertNotIn("_png", out)
            for values in ([.5, .4, .3, .2], [.10005, .5, .2002, .2]):
                detector = loc._Locator()
                detector._model = Model(values)
                with mock.patch.object(loc, "locator", detector):
                    out = loc.locate_middle(frames, "full_slice", True)
                self.assertEqual(out["status"], "shown")

    def test_disabled_setting_never_touches_model_or_pixels(self):
        cfg = import_module("class.knee.config")
        with mock.patch.object(cfg, "get_setting", return_value="0"), mock.patch.object(loc.locator, "detect") as detect, \
             mock.patch.object(loc.locator, "_load") as load, mock.patch.object(loc, "render_png") as render:
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(loc.locate_middle([], "full_slice", True)["status"], "disabled_by_policy")
            for value in ("", "0", "true", "yes", "2", "-1"):
                with mock.patch.dict(os.environ, {"KNEE_SCREEN_LOCALIZATION_ENABLED": value}):
                    self.assertEqual(loc.locate_middle([], "full_slice", True)["status"], "disabled_by_policy")
            detect.assert_not_called()
            load.assert_not_called()
            render.assert_not_called()

    def test_explicit_blank_environment_cannot_inherit_dotenv_on(self):
        cfg = import_module("class.knee.config")
        with mock.patch.object(cfg, "get_setting", return_value="1") as setting:
            for value in ("", "0", "false"):
                with mock.patch.dict(os.environ, {"KNEE_SCREEN_LOCALIZATION_ENABLED": value}):
                    self.assertFalse(loc.localization_enabled())
            setting.assert_not_called()
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertTrue(loc.localization_enabled())
            with mock.patch.dict(os.environ, {"KNEE_SCREEN_LOCALIZATION_ENABLED": "1"}):
                self.assertTrue(loc.localization_enabled())

    def test_cache_missing_and_sha_mismatch_are_unavailable(self):
        L = loc._Locator()
        cfg = import_module("class.knee.config")
        with mock.patch.object(cfg, "hf_repo", lambda: ""):
            with self.assertRaises(loc.LocationUnavailable) as e:
                L.detect(np.zeros((40, 40), np.uint8))
            self.assertEqual(e.exception.reason, "not_configured")
        import huggingface_hub
        with mock.patch.object(cfg, "hf_repo", lambda: "x/y"), mock.patch.object(huggingface_hub, "try_to_load_from_cache", lambda *a, **k: None):
            with self.assertRaises(loc.LocationUnavailable) as e:
                L.detect(np.zeros((40, 40), np.uint8))
            self.assertEqual(e.exception.reason, "cache_missing")
        tmp = Path(os.environ.get("TEMP", ".")) / "fake_acl_yolo.pt"
        tmp.write_bytes(b"not the pinned model")
        try:
            with mock.patch.object(cfg, "hf_repo", lambda: "x/y"), mock.patch.object(huggingface_hub, "try_to_load_from_cache", lambda *a, **k: str(tmp)):
                with self.assertRaises(loc.LocationUnavailable) as e:
                    L.detect(np.zeros((40, 40), np.uint8))
                self.assertEqual(e.exception.reason, "model_mismatch")
        finally:
            tmp.unlink()
        self.assertIsNone(L._model)

    def test_detect_is_serialized(self):
        L = loc._Locator()
        active, peak = [0], [0]

        class Boxes:
            def __len__(self):
                return 0

        class Res:
            boxes = Boxes()

        class Model:
            def predict(self, *a, **k):
                active[0] += 1; peak[0] = max(peak[0], active[0]); time.sleep(0.05); active[0] -= 1
                return [Res()]
        L._model = Model()
        ts = [threading.Thread(target=L.detect, args=(np.zeros((40, 40), np.uint8),)) for _ in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(peak[0], 1)


def _cached_yolo():
    try:
        from huggingface_hub import try_to_load_from_cache
        cfg = import_module("class.knee.config")
        return isinstance(try_to_load_from_cache(cfg.hf_repo(), loc.YOLO_FILE, revision=cfg.hf_revision()), str)
    except Exception:
        return False


@unittest.skipUnless(MANIFEST.exists() and _cached_yolo(), "로컬 KneeMRI PNG 또는 HF 캐시 YOLO 없음")
class RealYoloTests(unittest.TestCase):
    """고정 SHA 캐시 YOLO 가 기존 Localization.locate_acl 과 같은 박스를 내는지(같은 전·후처리)."""

    def test_same_as_existing_locate_acl(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        imageio = import_module("class.knee.imageio")
        Loc = import_module("class.knee.localization").Localization
        c = json.loads(MANIFEST.read_text(encoding="utf-8"))["cases"][:3]
        for case in c:
            mid = case["slice_count"] // 2
            g = imageio.decode_screen_image((DATA / case["paths"][mid].lstrip("/")).read_bytes()).gray
            mine = loc.locator.detect(g)
            ref = Loc.locate_acl(np.asarray(g, dtype=np.float64) / 255.0)
            if ref is None:
                self.assertIsNone(mine)
                continue
            for k in ("x_center", "y_center", "width", "height", "confidence"):
                self.assertAlmostEqual(mine[k], ref[k], places=4, msg=k)
        self.assertIsNotNone(loc.locator._model)


if __name__ == "__main__":
    unittest.main()
