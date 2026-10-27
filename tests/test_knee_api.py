"""
POST /api/v1/knee/screen-roi 계약 테스트 (설계 MSN-20260930-001).

DB 와 torch 없이 돈다. 라우터만 붙인 작은 앱으로 확인하고, 승인된 조합이 생겼을 때의 동작은 가짜 모델로 확인한다.
실행: python -m pytest tests/test_knee_api.py  (fastapi, pillow, numpy, httpx, python-multipart 필요)
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import io
import logging
import os
import threading
import time
import unittest
from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from routers.knee import router as knee_router

ROOT = Path(__file__).resolve().parents[1]
URL = "/api/v1/knee/screen-roi"
SECRET = "SECRET_PATIENT_NAME"

gates = importlib.import_module("class.knee.gates")
screen_roi = importlib.import_module("class.knee.screen_roi")
explanations = importlib.import_module("class.knee.explanations")
errors = importlib.import_module("class.knee.errors")


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(knee_router, prefix="/api/v1", tags=["knee"])
    return app


def _gradient(width: int = 200, height: int = 200) -> np.ndarray:
    x = np.linspace(0, 255, width, dtype=np.uint8)
    return np.tile(x, (height, 1))


def png_bytes(width: int = 200, height: int = 200, mode: str = "L") -> bytes:
    img = Image.fromarray(_gradient(width, height)).convert(mode)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def jpeg_bytes(width: int = 200, height: int = 200) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(_gradient(width, height)).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def screen_roi_verdict(name: str) -> str:
    return importlib.import_module("class.knee.constants").VERDICT[name][0]


def good_fields(**override) -> list[tuple]:
    fields = {"body_part": "knee", "plane": "sagittal", "image_scope": "full_slice"}
    fields.update(override)
    return [(k, (None, v)) for k, v in fields.items()]


def post(client, image: bytes | None = None, *, fields: list[tuple] | None = None, filename: str = "capture.png",
         ctype: str = "image/png", drop: tuple[str, ...] = (), extra: list[tuple] | None = None):
    parts = [p for p in (fields if fields is not None else good_fields()) if p[0] not in drop]
    if image is not None and "image" not in drop:
        parts.append(("image", (filename, image, ctype)))
    parts += extra or []
    return client.post(URL, files=parts)


ERROR_KEYS = {"request_id", "code", "message", "retryable"}


class _ApprovedGate:
    """승인된 조합이 있는 상황을 만드는 도우미(진짜 승인이 아니라 테스트용 가짜 기록)."""

    def __init__(self, test: unittest.TestCase):
        self.test = test

    def approve_finding(self, finding="abnormal", plane="sagittal", scope="full_slice", *, calibration=(1.0, 0.0),
                        band=(0.4, 0.6), performance=None, explanation=True, reviewed_by="테스트 검수"):
        explanation_id = f"{finding}_test"
        if explanation:
            explanations.EXPLANATIONS[explanation_id] = {"version": "t1", "text": "테스트용 고정 설명입니다.", "reviewed_by": "테스트"}
        gates.APPROVED[(finding, plane, scope)] = gates.GateRecord(
            finding=finding, plane=plane, scope=scope, validation_id="test-exp-1", validated_condition="테스트 조건",
            model_id="test-model", calibration=calibration, band=band,
            performance={"auc": 0.9} if performance is None else performance,
            explanation_id=explanation_id, reviewed_by=reviewed_by)

    def approve_location(self, plane="sagittal", scope="full_slice"):
        gates.APPROVED_LOCATION[(plane, scope)] = gates.LocationGate(
            plane=plane, scope=scope, validation_id="test-loc-1", validated_condition="테스트 조건",
            model_id="test-yolo", performance={"recall": 0.9}, reviewed_by="테스트 검수")


class KneeApiBase(unittest.TestCase):
    def setUp(self) -> None:
        self._save_flag = os.environ.get("KNEE_SCREEN_SAVE_ENABLED")
        os.environ["KNEE_SCREEN_SAVE_ENABLED"] = "0"
        self.client = TestClient(_app())
        self._saved = (dict(gates.APPROVED), dict(gates.APPROVED_LOCATION), dict(explanations.EXPLANATIONS))
        self._hooks = (screen_roi._predict_single, screen_roi._gradcam, screen_roi._locate_acl)
        self.calls: dict[str, int] = {"predict": 0, "gradcam": 0, "locate": 0}
        self.gate = _ApprovedGate(self)

        def fake_predict(finding, plane, gray):
            self.calls["predict"] += 1
            return self.raw_prob

        def fake_gradcam(finding, plane, gray):
            self.calls["gradcam"] += 1
            return Image.fromarray(np.zeros((224, 224, 3), dtype=np.uint8)), self.activated

        def fake_locate(gray):
            self.calls["locate"] += 1
            return self.box

        self.raw_prob, self.activated, self.box = 0.7, True, {
            "label": "acl_region", "x_center": 0.5, "y_center": 0.5, "width": 0.2, "height": 0.2, "confidence": 0.8}
        screen_roi._predict_single, screen_roi._gradcam, screen_roi._locate_acl = fake_predict, fake_gradcam, fake_locate

    def assert_error(self, response, status: int, code: str) -> dict:
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertEqual(set(body), ERROR_KEYS)
        self.assertEqual(body["code"], code)
        self.assertTrue(body["request_id"].startswith("req_"))
        self.assertIsInstance(body["retryable"], bool)
        return body

    def tearDown(self) -> None:
        gates.APPROVED.clear(); gates.APPROVED.update(self._saved[0])
        gates.APPROVED_LOCATION.clear(); gates.APPROVED_LOCATION.update(self._saved[1])
        explanations.EXPLANATIONS.clear(); explanations.EXPLANATIONS.update(self._saved[2])
        screen_roi._predict_single, screen_roi._gradcam, screen_roi._locate_acl = self._hooks
        if self._save_flag is None:
            os.environ.pop("KNEE_SCREEN_SAVE_ENABLED", None)
        else:
            os.environ["KNEE_SCREEN_SAVE_ENABLED"] = self._save_flag


class DefaultLockedTests(KneeApiBase):
    """승인표가 비어 있어도 모델이 있는 촬영면은 학습용 점수를 낸다. 검수 근거와 ACL 위치 박스는 비어 있다."""

    def test_gate_table_is_empty_by_default(self) -> None:
        self.assertEqual(self._saved[0], {})
        self.assertEqual(self._saved[1], {})
        self.assertEqual(self._saved[2], {})

    def test_planes_with_a_model_are_scored_without_an_approval_record(self) -> None:
        predict_calls = 0
        for plane in gates.PLANES:
            for scope in gates.SCOPES:
                with self.subTest(plane=plane, scope=scope):
                    response = post(self.client, png_bytes(), fields=good_fields(plane=plane, image_scope=scope))
                    self.assertEqual(response.status_code, 200)
                    body = response.json()
                    available = [name for name in gates.FINDINGS if gates.model_plane_available(name, plane)]
                    predict_calls += len(available)
                    self.assertEqual(body["analysis_status"], "evaluated" if len(available) == len(gates.FINDINGS) else "partially_evaluated")
                    self.assertEqual(body["body_part"], "knee")
                    self.assertEqual(body["image"], {"width": 200, "height": 200, "plane": plane, "image_scope": scope, "source": "screen_capture"})
                    self.assertEqual(set(body["findings"]), {"abnormal", "acl", "meniscus"})
                    for name, finding in body["findings"].items():
                        if name in available:
                            self.assertEqual(finding["status"], "evaluated")
                            self.assertEqual(finding["prob"], 0.7)
                            self.assertEqual(finding["verdict"], screen_roi_verdict(name))
                            self.assertEqual(finding["reason_code"], "single_image_unvalidated")
                        else:
                            self.assertEqual(finding["status"], "not_evaluated")
                            self.assertEqual(finding["reason_code"], "model_plane_unavailable")
                            self.assertIsNone(finding["prob"])
                    viz = body["visualization"]
                    self.assertEqual(viz["gradcam_status"], "shown")
                    self.assertTrue(viz["overlay_png_base64"])
                    self.assertEqual((viz["acl_location_status"], viz["acl_box"]), ("not_evaluated", None))
                    self.assertEqual(body["explanations"], [])
                    self.assertEqual(body["evidence"]["validated"], [])
                    self.assertEqual(body["evidence"]["gates_version"], gates.GATES_VERSION)
                    self.assertIsNone(body["review_idx"])
        self.assertEqual(self.calls, {"predict": predict_calls, "gradcam": len(gates.PLANES) * len(gates.SCOPES), "locate": 0})

    def test_meniscus_has_no_sagittal_model_and_missing_planes_are_reported(self) -> None:
        body = post(self.client, png_bytes(), fields=good_fields(plane="sagittal")).json()
        meniscus = body["findings"]["meniscus"]
        self.assertEqual(meniscus["reason_code"], "model_plane_unavailable")
        self.assertEqual(meniscus["missing_planes"], ["axial", "coronal"])
        self.assertEqual(body["findings"]["abnormal"]["status"], "evaluated")
        self.assertEqual(body["findings"]["abnormal"]["reason_code"], "single_image_unvalidated")
        self.assertEqual(body["findings"]["abnormal"]["missing_planes"], ["axial", "coronal"])
        axial = post(self.client, png_bytes(), fields=good_fields(plane="axial")).json()
        self.assertEqual(axial["findings"]["meniscus"]["status"], "evaluated")
        self.assertEqual(axial["findings"]["meniscus"]["reason_code"], "single_image_unvalidated")
        self.assertEqual(axial["findings"]["meniscus"]["missing_planes"], ["coronal"])

    def test_notices_and_retention_are_always_present(self) -> None:
        body = post(self.client, png_bytes()).json()
        codes = {n["code"] for n in body["notices"]}
        self.assertTrue({"single_capture", "visualization_meaning", "learning_only", "deidentify", "deletion"} <= codes)
        text = " ".join(n["text"] for n in body["notices"])
        self.assertIn("의료 판단 도구가 아니라", text)
        self.assertIn("전문가와 상의", text)
        self.assertEqual(body["retention"]["input_deleted"], True)
        self.assertEqual(body["retention"]["transient_only"], True)
        self.assertIn("원본의 삭제를 뜻하지 않습니다", body["retention"]["scope_note"])
        roi = post(self.client, png_bytes(), fields=good_fields(image_scope="roi_crop")).json()
        self.assertIn("roi_crop", {n["code"] for n in roi["notices"]})


class RequestValidationTests(KneeApiBase):
    def test_missing_required_fields(self) -> None:
        for field in ("body_part", "plane", "image_scope", "image"):
            with self.subTest(field=field):
                self.assert_error(post(self.client, png_bytes(), drop=(field,)), 400, "missing_field")

    def test_duplicate_fields(self) -> None:
        self.assert_error(post(self.client, png_bytes(), extra=[("plane", (None, "axial"))]), 400, "duplicate_field")
        self.assert_error(post(self.client, png_bytes(), extra=[("image", ("b.png", png_bytes(), "image/png"))]), 400, "duplicate_field")

    def test_unknown_field_is_rejected(self) -> None:
        self.assert_error(post(self.client, png_bytes(), extra=[("patient_name", (None, "홍길동"))]), 400, "unknown_field")

    def test_wrong_body_part_never_reaches_the_knee_model(self) -> None:
        for value in ("brain", "Knee", "", "knee "):
            with self.subTest(value=value):
                self.assert_error(post(self.client, png_bytes(), fields=good_fields(body_part=value)), 400, "invalid_body_part")
        self.assertEqual(self.calls["predict"], 0)

    def test_invalid_plane_scope_and_boolean(self) -> None:
        self.assert_error(post(self.client, png_bytes(), fields=good_fields(plane="oblique")), 400, "invalid_plane")
        self.assert_error(post(self.client, png_bytes(), fields=good_fields(image_scope="whole")), 400, "invalid_image_scope")
        self.assert_error(post(self.client, png_bytes(), fields=good_fields(include_overlay="maybe")), 400, "invalid_boolean")
        self.assertEqual(post(self.client, png_bytes(), fields=good_fields(include_overlay="false")).status_code, 200)
        self.assertEqual(post(self.client, png_bytes(), fields=good_fields(include_overlay="TRUE")).status_code, 200)

    def test_image_must_be_a_file_and_body_must_be_multipart(self) -> None:
        self.assert_error(post(self.client, None, extra=[("image", (None, "not a file"))]), 400, "image_not_file")
        self.assert_error(self.client.post(URL, json={"plane": "sagittal"}), 400, "invalid_form")

    def test_oversized_request_is_rejected_before_parsing(self) -> None:
        self.assert_error(post(self.client, b"\x89PNG\r\n\x1a\n" + b"0" * (6 * 1024 * 1024)), 413, "payload_too_large")

    def test_feature_flag_can_close_the_endpoint(self) -> None:
        import os

        os.environ["KNEE_SCREEN_ROI_ENABLED"] = "0"
        try:
            self.assert_error(post(self.client, png_bytes()), 503, "feature_disabled")
        finally:
            os.environ.pop("KNEE_SCREEN_ROI_ENABLED", None)


class ImageValidationTests(KneeApiBase):
    def test_unsupported_formats_are_415(self) -> None:
        gif = io.BytesIO()
        Image.fromarray(_gradient()).save(gif, "GIF")
        bmp = io.BytesIO()
        Image.fromarray(_gradient()).save(bmp, "BMP")
        webp = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"0" * 40
        for name, data, ctype in (("gif", gif.getvalue(), "image/gif"), ("bmp", bmp.getvalue(), "image/bmp"),
                                  ("webp", webp, "image/webp"), ("text", b"hello, this is text", "text/plain"),
                                  ("fake_png_name", b"<html>x</html>", "image/png")):
            with self.subTest(name=name):
                self.assert_error(post(self.client, data, filename=f"a.{name}.png", ctype=ctype), 415, "unsupported_media_type")

    def test_broken_and_empty_images_are_422(self) -> None:
        good = png_bytes()
        self.assert_error(post(self.client, good[: len(good) // 2]), 422, "decode_failed")
        self.assert_error(post(self.client, b"\x89PNG\r\n\x1a\n"), 422, "decode_failed")
        self.assert_error(post(self.client, b"\xff\xd8\xff\xe0garbage"), 422, "decode_failed")
        self.assert_error(post(self.client, b""), 422, "empty_image")

    def test_size_limits(self) -> None:
        self.assert_error(post(self.client, png_bytes(32, 32)), 422, "image_too_small")
        self.assertEqual(post(self.client, png_bytes(64, 64)).status_code, 200)
        self.assertEqual(post(self.client, png_bytes(1024, 1024)).status_code, 200)
        self.assert_error(post(self.client, png_bytes(1025, 100)), 413, "image_too_large")
        self.assert_error(post(self.client, png_bytes(3000, 3000)), 413, "image_too_large")

    def test_blank_animated_and_odd_pixel_formats(self) -> None:
        blank = io.BytesIO()
        Image.fromarray(np.full((200, 200), 90, dtype=np.uint8)).save(blank, "PNG")
        self.assert_error(post(self.client, blank.getvalue()), 422, "blank_image")

        frames = [Image.fromarray(_gradient()), Image.fromarray(255 - _gradient())]
        animated = io.BytesIO()
        frames[0].save(animated, "PNG", save_all=True, append_images=frames[1:])
        self.assert_error(post(self.client, animated.getvalue()), 422, "animated_image")

        sixteen = io.BytesIO()
        Image.fromarray((_gradient().astype(np.uint16) * 257)).save(sixteen, "PNG")
        self.assert_error(post(self.client, sixteen.getvalue()), 422, "unsupported_pixel_format")

    def test_normal_color_alpha_palette_and_jpeg_images_are_accepted(self) -> None:
        rgba = io.BytesIO()
        img = Image.fromarray(_gradient()).convert("RGBA")
        img.putalpha(Image.fromarray(_gradient()))
        img.save(rgba, "PNG")
        palette = io.BytesIO()
        Image.fromarray(_gradient()).convert("P").save(palette, "PNG")
        for name, data, ctype in (("rgb", png_bytes(mode="RGB"), "image/png"), ("rgba", rgba.getvalue(), "image/png"),
                                  ("palette", palette.getvalue(), "image/png"), ("jpeg", jpeg_bytes(), "image/jpeg")):
            with self.subTest(name=name):
                self.assertEqual(post(self.client, data, ctype=ctype).status_code, 200)

    def test_decoder_returns_grayscale_without_metadata(self) -> None:
        imageio = importlib.import_module("class.knee.imageio")
        decoded = imageio.decode_screen_image(png_bytes(120, 80, "RGB"))
        self.assertEqual((decoded.gray.dtype, decoded.gray.shape, decoded.width, decoded.height), (np.uint8, (80, 120), 120, 80))
        self.assertEqual(decoded.steps["metadata"], "discarded")
        self.assertEqual(decoded.steps["resize"], "none")


class SafeErrorBodyTests(KneeApiBase):
    def test_no_upload_values_filenames_or_exception_text_leak(self) -> None:
        logger_output = io.StringIO()
        handler = logging.StreamHandler(logger_output)
        logging.getLogger().addHandler(handler)
        try:
            cases = [
                post(self.client, b"not an image at all " + SECRET.encode(), filename=f"{SECRET}.png"),
                post(self.client, png_bytes(), filename=f"{SECRET}.png", fields=good_fields(plane="oblique")),
                post(self.client, png_bytes(32, 32), filename=f"{SECRET}.png"),
                post(self.client, png_bytes(), filename=f"{SECRET}.png"),
            ]
            for response in cases:
                self.assertNotIn(SECRET, response.text)
                self.assertNotIn("Traceback", response.text)
            for response in cases[:3]:
                self.assertEqual(set(response.json()), ERROR_KEYS)
        finally:
            logging.getLogger().removeHandler(handler)
        self.assertNotIn(SECRET, logger_output.getvalue())

    def test_unexpected_errors_are_500_with_fixed_text_and_no_content_in_logs(self) -> None:
        self.gate.approve_finding()
        secret_text = "SECRET_PATH C:\\models\\token=abc123"

        def boom(finding, plane, gray):
            raise RuntimeError(secret_text)

        screen_roi._predict_single = boom
        with self.assertLogs("knee_api", level="ERROR") as logs:
            response = post(self.client, png_bytes())
        body = self.assert_error(response, 500, "internal_error")
        self.assertNotIn("SECRET", response.text)
        self.assertNotIn("abc123", " ".join(logs.output))
        self.assertIn("RuntimeError", " ".join(logs.output))  # 종류만 기록
        self.assertFalse(body["retryable"])

    def test_model_not_ready_is_503_and_input_error_is_422(self) -> None:
        self.gate.approve_finding()

        def not_ready(finding, plane, gray):
            raise errors.KneeModelNotReady("내부 저장소 이름 my-secret-repo")

        screen_roi._predict_single = not_ready
        response = post(self.client, png_bytes())
        body = self.assert_error(response, 503, "model_unavailable")
        self.assertTrue(body["retryable"])
        self.assertNotIn("my-secret-repo", response.text)

        def bad_input(finding, plane, gray):
            raise errors.KneeInputError("내부 상세")

        screen_roi._predict_single = bad_input
        self.assert_error(post(self.client, png_bytes()), 422, "invalid_image")

    def test_timeout_is_504_and_retryable(self) -> None:
        import os

        self.gate.approve_finding()

        def slow(finding, plane, gray):
            time.sleep(0.4)
            return 0.7

        screen_roi._predict_single = slow
        os.environ["KNEE_SCREEN_ROI_TIMEOUT_S"] = "0.05"
        try:
            body = self.assert_error(post(self.client, png_bytes()), 504, "timeout")
        finally:
            os.environ.pop("KNEE_SCREEN_ROI_TIMEOUT_S", None)
        self.assertTrue(body["retryable"])


class ApprovedGateTests(KneeApiBase):
    """게이트가 승인한 조합에서만 점수가 나오고, 나머지는 여전히 비어 있다(AC-04)."""

    def test_only_the_approved_finding_is_evaluated(self) -> None:
        self.gate.approve_finding("abnormal", "sagittal", "full_slice", calibration=(1.0, 0.0))
        body = post(self.client, png_bytes()).json()
        self.assertEqual(body["analysis_status"], "partially_evaluated")
        abnormal = body["findings"]["abnormal"]
        self.assertEqual((abnormal["status"], abnormal["prob"], abnormal["confidence_band"]), ("evaluated", 0.7, "confident"))
        self.assertEqual(abnormal["verdict"], "이상 소견 가능성 높음")
        self.assertEqual(abnormal["planes_used"], ["sagittal"])
        self.assertIsNone(abnormal["reason_code"])
        self.assertIn("임상적 발병 확률이 아님", abnormal["score_label"])
        self.assertEqual(body["findings"]["acl"]["status"], "evaluated")
        self.assertEqual(body["findings"]["acl"]["reason_code"], "single_image_unvalidated")
        self.assertEqual(body["findings"]["meniscus"]["status"], "not_evaluated")
        self.assertIsNone(body["findings"]["meniscus"]["prob"])
        self.assertEqual(self.calls["predict"], 2)  # 시상면 모델이 있는 이상·ACL. 반월판 모델은 없다
        self.assertEqual(body["explanations"], [{"finding": "abnormal", "id": "abnormal_test", "version": "t1", "text": "테스트용 고정 설명입니다."}])
        self.assertEqual(len(body["evidence"]["validated"]), 1)
        self.assertEqual(body["evidence"]["validated"][0]["validation_id"], "test-exp-1")
        self.assertEqual(body["evidence"]["validated"][0]["performance"], {"auc": 0.9})

    def test_unapproved_planes_still_return_a_learning_score(self) -> None:
        self.gate.approve_finding("abnormal", "sagittal", "full_slice")
        for plane, scope in (("axial", "full_slice"), ("coronal", "full_slice"), ("sagittal", "roi_crop")):
            with self.subTest(plane=plane, scope=scope):
                body = post(self.client, png_bytes(), fields=good_fields(plane=plane, image_scope=scope)).json()
                self.assertIn(body["analysis_status"], ("evaluated", "partially_evaluated"))
                self.assertEqual(body["findings"]["abnormal"]["status"], "evaluated")
                self.assertEqual(body["findings"]["abnormal"]["reason_code"], "single_image_unvalidated")
                self.assertEqual(body["evidence"]["validated"], [])

    def test_meniscus_is_never_produced_from_sagittal(self) -> None:
        self.gate.approve_finding("meniscus", "sagittal", "full_slice")
        body = post(self.client, png_bytes()).json()
        self.assertEqual(body["findings"]["meniscus"]["status"], "not_evaluated")
        self.assertEqual(body["findings"]["meniscus"]["reason_code"], "model_plane_unavailable")
        self.assertIsNone(body["findings"]["meniscus"]["prob"])

    def test_incomplete_approvals_are_ignored(self) -> None:
        for kwargs in ({"performance": {}}, {"explanation": False}, {"reviewed_by": ""}):
            with self.subTest(kwargs=kwargs):
                gates.APPROVED.clear()
                explanations.EXPLANATIONS.clear()
                self.gate.approve_finding("abnormal", "sagittal", "full_slice", **kwargs)
                body = post(self.client, png_bytes()).json()
                self.assertEqual(body["findings"]["abnormal"]["reason_code"], "single_image_unvalidated")
                self.assertEqual(body["findings"]["abnormal"]["prob"], 0.7)
                self.assertEqual(body["evidence"]["validated"], [])
        self.assertGreater(self.calls["predict"], 0)

    def test_gate_specific_calibration_and_band_are_used(self) -> None:
        self.gate.approve_finding("abnormal", "sagittal", "full_slice", calibration=(2.0, 0.0), band=(0.4, 0.9))
        abnormal = post(self.client, png_bytes()).json()["findings"]["abnormal"]
        self.assertAlmostEqual(abnormal["prob"], 0.845, places=3)
        self.assertEqual((abnormal["confidence_band"], abnormal["verdict"]), ("uncertain", "판단 어려움"))
        self.raw_prob = 0.3
        self.assertEqual(post(self.client, png_bytes()).json()["findings"]["abnormal"]["verdict"], "이상 소견 가능성 낮음")

    def test_gradcam_only_when_evaluated_and_activated(self) -> None:
        self.gate.approve_finding()
        viz = post(self.client, png_bytes()).json()["visualization"]
        self.assertEqual((viz["gradcam_status"], viz["gradcam_finding"]), ("shown", "abnormal"))
        self.assertTrue(base64.b64decode(viz["overlay_png_base64"]).startswith(b"\x89PNG"))
        self.assertIn("224", viz["overlay_note"])

        self.activated = False
        viz = post(self.client, png_bytes()).json()["visualization"]
        self.assertEqual((viz["gradcam_status"], viz["overlay_png_base64"]), ("no_activation", None))

        self.activated = True
        before = self.calls["gradcam"]
        viz = post(self.client, png_bytes(), fields=good_fields(include_overlay="false")).json()["visualization"]
        self.assertEqual((viz["gradcam_status"], viz["overlay_png_base64"]), ("not_evaluated", None))
        self.assertEqual(self.calls["gradcam"], before)

    def test_acl_box_only_for_approved_sagittal_full_slice(self) -> None:
        self.gate.approve_location("sagittal", "full_slice")
        viz = post(self.client, png_bytes()).json()["visualization"]
        self.assertEqual(viz["acl_location_status"], "shown")
        self.assertEqual(viz["acl_box"]["label"], "acl_region")
        self.assertIn("파열 위치가 아닙니다", viz["acl_box_note"])
        self.assertEqual(self.calls["locate"], 1)

        for plane, scope in (("axial", "full_slice"), ("sagittal", "roi_crop")):
            self.gate.approve_location(plane, scope)  # 표에 잘못 올라가 있어도 코드가 다시 막는다
            viz = post(self.client, png_bytes(), fields=good_fields(plane=plane, image_scope=scope)).json()["visualization"]
            self.assertEqual((viz["acl_location_status"], viz["acl_box"]), ("not_evaluated", None))
        self.assertEqual(self.calls["locate"], 1)

        self.box = None
        viz = post(self.client, png_bytes()).json()["visualization"]
        self.assertEqual((viz["acl_location_status"], viz["acl_box"]), ("not_found", None))


class CleanupTests(KneeApiBase):
    """AC-07: 성공·실패 어느 경로에서도 업로드를 닫고, 삭제에 실패하면 성공으로 답하지 않는다."""

    def setUp(self) -> None:
        super().setUp()
        from starlette.datastructures import UploadFile

        self.UploadFile = UploadFile
        self.closed = 0
        self.fail_close = False
        original = UploadFile.close
        outer = self

        async def spy(this):
            outer.closed += 1
            if outer.fail_close:
                raise OSError("close failed")
            await original(this)

        self._original_close = original
        UploadFile.close = spy

    def tearDown(self) -> None:
        self.UploadFile.close = self._original_close
        super().tearDown()

    def test_upload_is_closed_on_success_and_on_every_error(self) -> None:
        scenarios = [
            ("success", lambda: post(self.client, png_bytes()), 200),
            ("bad_plane", lambda: post(self.client, png_bytes(), fields=good_fields(plane="x")), 400),
            ("not_image", lambda: post(self.client, b"hello"), 415),
            ("too_small", lambda: post(self.client, png_bytes(32, 32)), 422),
            ("too_large", lambda: post(self.client, png_bytes(3000, 3000)), 413),
        ]
        for name, call, status in scenarios:
            with self.subTest(name=name):
                self.closed = 0
                self.assertEqual(call().status_code, status)
                self.assertGreaterEqual(self.closed, 1, "업로드 파일을 닫지 않았습니다")

    def test_upload_is_closed_when_the_model_fails(self) -> None:
        self.gate.approve_finding()
        screen_roi._predict_single = lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
        self.closed = 0
        self.assertEqual(post(self.client, png_bytes()).status_code, 500)
        self.assertGreaterEqual(self.closed, 1)

    def test_failed_deletion_is_not_reported_as_success(self) -> None:
        self.fail_close = True
        response = post(self.client, png_bytes())
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertEqual(body["code"], "cleanup_failed")
        self.assertNotIn("analysis_status", body)
        self.assertNotIn("retention", body)

    def test_input_deleted_is_true_only_after_cleanup(self) -> None:
        self.assertEqual(post(self.client, png_bytes()).json()["retention"]["input_deleted"], True)


class ConcurrencyAndWiringTests(KneeApiBase):
    def test_parallel_requests_do_not_mix_results(self) -> None:
        self.gate.approve_finding("abnormal", "axial", "full_slice")
        results: dict[int, dict] = {}

        def worker(i: int) -> None:
            client = TestClient(_app())
            plane = "axial" if i % 2 == 0 else "sagittal"
            response = post(client, png_bytes(100 + i, 100 + i), fields=good_fields(plane=plane))
            results[i] = {"status": response.status_code, "body": response.json(), "plane": plane}

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(results), 8)
        ids = set()
        for i, r in results.items():
            self.assertEqual(r["status"], 200)
            self.assertEqual(r["body"]["image"]["width"], 100 + i)
            self.assertEqual(r["body"]["image"]["plane"], r["plane"])
            self.assertEqual(r["body"]["findings"]["abnormal"]["status"], "evaluated")
            ids.add(r["body"]["request_id"])
        self.assertEqual(len(ids), 8)

    def test_router_is_thin_and_registered_in_main(self) -> None:
        source = (ROOT / "routers" / "knee.py").read_text(encoding="utf-8")
        for banned in ("import torch", "get_db", "Session", "nas_service", "nasServices", "upload_analysis_bundle", "print("):
            self.assertNotIn(banned, source, f"라우터에 {banned!r} 가 있으면 안 됩니다")
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("from routers import admin, commonness, brain, brain_mri, knee, kneeLearning, user, account, thorax", main)
        self.assertIn('app.include_router(knee.router, prefix="/api/v1", tags=["knee"])', main)
        self.assertIn("app.include_router(kneeLearning.router)", main)
        self.assertNotIn("class.knee", main)  # 앱 시작 때 무릎 모델을 불러오지 않는다

    def test_openapi_lists_the_endpoint_with_error_models(self) -> None:
        spec = self.client.get("/openapi.json").json()
        operation = spec["paths"][URL]["post"]
        self.assertEqual(set(operation["responses"]), {"200", "400", "413", "415", "422", "500", "503", "504"})


if __name__ == "__main__":
    unittest.main()
