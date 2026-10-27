"""뇌 MRI 5질환(YOLO26s-Seg) 분석 API 테스트.

- 모델이 없어도 도는 테스트: 전처리, 503 처리, health, 기존 CT 라우트 유지.
- BRAIN_MRI_MODEL_PATH 가 실제 파일일 때만 도는 테스트: 실제 추론.
  BRAIN_MRI_TEST_IMAGE 에 라벨 있는 MRI 슬라이스 경로를 주면 검출 1건 이상도 확인한다.
  그 파일이 sample_vs.png(TCIA VS-SEG, CC BY 4.0)면 독립 API 결과와 일치하는지도 본다.

TestClient 를 with 없이 쓰므로 lifespan(DB 대기)은 실행하지 않는다.
"""

from __future__ import annotations

import io
import os
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from main import app

brain_mri = import_module("class.brain_mri")
mri_config = import_module("class.brain_mri.config")
mri_constants = import_module("class.brain_mri.constants")

EXPECTED_CLASSES = [
    "vestibular_schwannoma",
    "glioma",
    "brain_metastasis",
    "ischemic_stroke",
    "multiple_sclerosis",
]
HAS_MODEL = bool(mri_config.MODEL_PATH) and Path(mri_config.MODEL_PATH).is_file()
TEST_IMAGE = os.environ.get("BRAIN_MRI_TEST_IMAGE", "")


def _png_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


class BrainMriPreprocessTests(unittest.TestCase):
    def test_class_order_is_fixed(self) -> None:
        self.assertEqual(mri_constants.CLASSES, EXPECTED_CLASSES)

    def test_png_becomes_gray_3channel(self) -> None:
        rgb = np.zeros((40, 30, 3), dtype=np.uint8)
        rgb[..., 0] = 200
        img = brain_mri.Preprocess.load_image_uint8_from_bytes(_png_bytes(rgb), "a.png")
        self.assertEqual(img.shape, (40, 30, 3))
        self.assertTrue((img[..., 0] == img[..., 1]).all())

    def test_png_pixels_are_not_renormalized(self) -> None:
        """학습 PNG 는 통합 단계에서 그대로 복사됐다. 8-bit slice 값은 바꾸지 않는다."""
        gray = (np.arange(64 * 64) % 256).astype(np.uint8).reshape(64, 64)
        img = brain_mri.Preprocess.load_image_uint8_from_bytes(_png_bytes(gray), "a.png")
        self.assertTrue((img[..., 0] == gray).all())

    def test_dicom_and_16bit_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            brain_mri.Preprocess.load_image_uint8_from_bytes(b"0" * 200, "a.dcm")
        buf = io.BytesIO()
        Image.fromarray(np.full((32, 32), 4000, dtype=np.uint16)).save(buf, format="PNG")
        with self.assertRaises(ValueError):
            brain_mri.Preprocess.load_image_uint8_from_bytes(buf.getvalue(), "a.png")

    def test_trained_sequences_cover_all_classes(self) -> None:
        self.assertEqual(set(mri_constants.TRAINED_SEQUENCES), set(EXPECTED_CLASSES))


class BrainMriApiWithoutModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_missing_model_returns_503_only_for_mri(self) -> None:
        empty = brain_mri.SegmentationService(model_path="")
        with patch.object(brain_mri, "segmentation_service", empty), patch.object(mri_config, "hf_repo", lambda: ""):
            r = self.client.post(
                "/api/v1/analyze/brain-mri",
                files={"image": ("a.png", _png_bytes(np.zeros((64, 64), np.uint8)), "image/png")},
            )
        self.assertEqual(r.status_code, 503)
        self.assertIn("BRAIN_MRI_MODEL_PATH", r.json()["detail"])

    def test_bad_image_returns_400(self) -> None:
        r = self.client.post(
            "/api/v1/analyze/brain-mri",
            files={"image": ("a.png", b"\x89PNG" + b"0" * 100, "image/png")},
        )
        self.assertEqual(r.status_code, 400)

    def test_dicom_returns_415(self) -> None:
        r = self.client.post(
            "/api/v1/analyze/brain-mri",
            files={"image": ("slice.dcm", b"DICM" + b"0" * 200, "application/dicom")},
        )
        self.assertEqual(r.status_code, 415)

    def test_invalid_sequence_returns_400(self) -> None:
        r = self.client.post(
            "/api/v1/analyze/brain-mri",
            files={"image": ("a.png", _png_bytes(np.zeros((64, 64), np.uint8)), "image/png")},
            data={"sequence": "CT"},
        )
        self.assertEqual(r.status_code, 400)

    def test_missing_field_returns_422(self) -> None:
        r = self.client.post("/api/v1/analyze/brain-mri")
        self.assertEqual(r.status_code, 422)

    def test_health_always_200(self) -> None:
        r = self.client.get("/api/v1/brain-mri/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["classes"], EXPECTED_CLASSES)

    def test_ct_routes_still_registered(self) -> None:
        paths = set(app.openapi()["paths"])
        self.assertIn("/api/v1/analyze/screen-roi", paths)
        self.assertIn("/api/v1/analyze/screen-roi/explain", paths)
        self.assertIn("/api/v1/health", paths)
        self.assertIn("/api/v1/analyze/brain-mri", paths)


@unittest.skipUnless(HAS_MODEL, "BRAIN_MRI_MODEL_PATH 가중치 파일이 없음")
class BrainMriApiWithModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def _post(self, data: bytes, name: str = "mri.png"):
        return self.client.post(
            "/api/v1/analyze/brain-mri", files={"image": (name, data, "image/png")}
        )

    def test_blank_image_returns_empty_predictions(self) -> None:
        r = self._post(_png_bytes(np.zeros((256, 256), np.uint8)))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["predictions"], [])
        self.assertFalse(body["is_diagnosis"])
        self.assertEqual(body["sequence"], "unknown")
        self.assertTrue(body["limitations"])
        self.assertEqual(body["trained_sequences"]["ischemic_stroke"], "DWI")
        self.assertIsNotNone(body["overlay_png_base64"])

    @unittest.skipUnless(TEST_IMAGE and Path(TEST_IMAGE).is_file(), "BRAIN_MRI_TEST_IMAGE 없음")
    def test_sample_slice_returns_valid_predictions(self) -> None:
        r = self._post(Path(TEST_IMAGE).read_bytes(), Path(TEST_IMAGE).name)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertGreaterEqual(len(body["predictions"]), 1)
        w, h = body["image_width"], body["image_height"]
        for p in body["predictions"]:
            self.assertEqual(p["disease"], EXPECTED_CLASSES[p["class_id"]])
            self.assertTrue(0.0 <= p["confidence"] <= 1.0)
            x1, y1, x2, y2 = p["bbox"]
            self.assertTrue(0 <= x1 <= x2 <= w + 1 and 0 <= y1 <= y2 <= h + 1)
            self.assertGreaterEqual(len(p["polygon"]), 3)

    @unittest.skipUnless(
        TEST_IMAGE and Path(TEST_IMAGE).name == "sample_vs.png", "VS 샘플(sample_vs.png) 없음"
    )
    def test_vs_sample_matches_standalone_api(self) -> None:
        """독립 brain-mri-ai-api 의 같은 샘플 결과와 같아야 한다 (2026-10-21 비교 값)."""
        r = self.client.post(
            "/api/v1/analyze/brain-mri",
            files={"image": ("sample_vs.png", Path(TEST_IMAGE).read_bytes(), "image/png")},
            data={"sequence": "T1"},
        )
        self.assertEqual(r.status_code, 200)
        preds = r.json()["predictions"]
        self.assertEqual(len(preds), 1)
        p = preds[0]
        self.assertEqual(p["class_id"], 0)
        self.assertAlmostEqual(p["confidence"], 0.8678, delta=0.005)
        for got, want in zip(p["bbox"], [206.4, 297.7, 230.6, 323.8]):
            self.assertAlmostEqual(got, want, delta=1.0)
        self.assertEqual(len(p["polygon"]), 22)
        self.assertEqual(r.json()["sequence"], "T1")


class BrainMriGradeTests(unittest.TestCase):
    def test_overlapping_box_is_correct(self) -> None:
        from services.brainMriServices import grade_box

        grade = grade_box(
            100,
            100,
            [{
                "disease": "glioma",
                "disease_ko": "신경교종",
                "confidence": 0.8,
                "bbox": [20, 20, 60, 60],
            }],
            x=0.2,
            y=0.2,
            box_w=0.4,
            box_h=0.4,
        )
        self.assertEqual(grade["result"], "정답")
        self.assertGreaterEqual(grade["iou"], 0.4)

    def test_empty_detection_is_wrong(self) -> None:
        from services.brainMriServices import grade_box

        grade = grade_box(100, 100, [], x=0.2, y=0.2, box_w=0.4, box_h=0.4)
        self.assertEqual(grade["result"], "오답")
        self.assertIsNone(grade["reference_bbox"])


if __name__ == "__main__":
    unittest.main()
