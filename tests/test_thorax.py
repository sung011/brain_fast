"""흉부 X-ray 소견 탐지(class/thorax)·API·ROI 채점 분기를 검증한다.

대부분은 정해진 박스를 돌려주는 가짜 탐지 모델로 확인한다 (torch·ultralytics 불필요).
THORAX_TEST_LOCAL_DIR 에 실제 모델 패키지 폴더를 주면 실제 모델로도 한 번 돌려 본다.
"""
import io
import json
import os
from importlib import import_module
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

thorax = import_module("class.thorax")
constants = import_module("class.thorax.constants")
thorax_config = import_module("class.thorax.config")

from models.studyModel import StudyModel  # noqa: E402
from routers.thorax import router  # noqa: E402
from services import learningServices, roiGradeServices  # noqa: E402

SIZE = 200


def chest_png(size: int = SIZE) -> bytes:
    """검은 배경 위에 흉곽과 두 폐야를 흉내 낸 흑백 PNG (실제 X-ray 아님)."""
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)
    draw.rectangle([12, 12, size - 12, size - 12], fill=170)
    draw.ellipse([25, 25, size // 2 - 5, size - 30], fill=60)
    draw.ellipse([size // 2 + 5, 25, size - 25, size - 30], fill=60)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _Values:
    def __init__(self, values):
        self.values = values

    def cpu(self):
        return self

    def tolist(self):
        return list(self.values)


class _Boxes:
    def __init__(self, rows):
        self.xyxy = _Values([r[0] for r in rows])
        self.conf = _Values([r[1] for r in rows])
        self.cls = _Values([r[2] for r in rows])

    def __len__(self):
        return len(self.conf.values)


class FakePredictor:
    """ultralytics YOLO 대신 정해진 박스를 돌려준다. rows: [(xyxy, conf, cls), ...]"""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def predict(self, img, **kwargs):
        self.calls.append(kwargs)
        return [type("Result", (), {"boxes": _Boxes(self.rows)})()]


def fake_models(opacity_rows=None, effusion_rows=None):
    opacity = thorax.FindingModel(
        "opacity", {"class_names": {"0": "airspace_opacity"}, "image_size": 768,
                    "confidence_threshold": 0.05, "nms_iou_threshold": 0.7, "max_detections": 100},
        FakePredictor(opacity_rows or []))
    # 실제 흉막삼출 모델처럼 설정 파일의 이름을 쓰는지 본다 (모델 안 이름은 'item')
    effusion = thorax.FindingModel(
        "effusion", {"class_names": {"0": "Pleural effusion"}, "input_image_size": 1024,
                     "confidence_threshold": 0.06, "nms_iou_threshold": 0.5, "max_detections": 10},
        FakePredictor(effusion_rows or []))
    return [opacity, effusion]


@pytest.fixture
def detector(monkeypatch):
    """폐음영 박스 1개(왼쪽 위), 흉막삼출 박스 1개(오른쪽 아래)를 돌려주는 탐지기."""
    service = thorax.ThoraxDetector()
    service.use_models(fake_models(
        opacity_rows=[((20, 20, 80, 80), 0.42, 0)],
        effusion_rows=[((120, 130, 190, 190), 0.31, 0)],
    ))
    monkeypatch.setattr(thorax, "detector_service", service)
    return service


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/thorax")
    with TestClient(app) as c:
        yield c


def test_labels_support_languages_and_fallback():
    assert constants.class_label("pneumothorax") == "기흉"
    assert constants.class_label("pneumothorax", "en") == "Pneumothorax"
    assert constants.class_label("pneumothorax", "ja") == "기흉"  # 없는 언어는 한국어
    assert constants.class_label("unknown_finding") == "unknown_finding"
    assert all(code in constants.LABELS for code in constants.CLASSES)


@pytest.mark.parametrize("name,code", [("Pleural effusion", "pleural_effusion"),
                                       ("airspace_opacity", "airspace_opacity"),
                                       ("Lung Opacity", "airspace_opacity"),
                                       ("Pneumothorax", "pneumothorax")])
def test_model_names_map_to_finding_codes(name, code):
    assert constants.finding_code(name) == code


def test_png_becomes_three_channel_gray():
    img = thorax.Preprocess.load_image_uint8_from_bytes(chest_png(), "roi.png")
    assert img.shape == (SIZE, SIZE, 3) and img.dtype == np.uint8
    assert (img[..., 0] == img[..., 1]).all() and (img[..., 1] == img[..., 2]).all()


def test_dicom_monochrome1_is_inverted():
    pydicom = pytest.importorskip("pydicom")
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    arr = np.tile(np.arange(64, dtype=np.uint16) * 50, (64, 1))
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = generate_uid()
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = Dataset()
    ds.file_meta = meta
    ds.Rows, ds.Columns = arr.shape
    ds.SamplesPerPixel = 1
    ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 16, 15
    ds.PixelRepresentation = 0
    ds.PixelData = arr.tobytes()

    def dicom_bytes(photometric: str) -> bytes:
        ds.PhotometricInterpretation = photometric
        buf = io.BytesIO()
        try:  # pydicom 3.x
            ds.save_as(buf, enforce_file_format=True)
        except TypeError:  # pydicom 2.x
            ds.save_as(buf, write_like_original=False)
        return buf.getvalue()

    load = thorax.Preprocess.load_image_uint8_from_bytes
    mono2 = load(dicom_bytes("MONOCHROME2"), "x.dcm")[..., 0]
    mono1 = load(dicom_bytes("MONOCHROME1"), "x.dcm")[..., 0]
    assert mono2[0, -1] > mono2[0, 0]  # 오른쪽이 밝다
    assert mono1[0, -1] < mono1[0, 0]  # 반전되어 오른쪽이 어둡다


def test_package_settings_are_passed_to_model(detector):
    detector.detect(np.zeros((SIZE, SIZE, 3), dtype=np.uint8))
    opacity, effusion = detector.models
    assert opacity.predictor.calls[0] == {"imgsz": 768, "conf": 0.05, "iou": 0.7, "max_det": 100, "verbose": False}
    assert effusion.predictor.calls[0]["imgsz"] == 1024  # input_image_size 키도 읽는다
    assert detector.classes == ["airspace_opacity", "pleural_effusion"]


def test_scores_are_best_box_confidence_per_finding():
    service = thorax.ThoraxDetector()
    service.use_models(fake_models(opacity_rows=[((0, 0, 10, 10), 0.2, 0), ((5, 5, 20, 20), 0.6, 0)]))
    detections = service.detect(np.zeros((SIZE, SIZE, 3), dtype=np.uint8))
    assert service.scores(detections).tolist() == pytest.approx([0.6, 0.0])


def test_without_model_settings_api_answers_503(client, monkeypatch):
    monkeypatch.setattr(thorax_config, "PACKAGES", [])
    monkeypatch.setattr(thorax, "detector_service", thorax.ThoraxDetector())
    response = client.post("/api/v1/thorax/analyze/screen-roi",
                           files={"image": ("roi.png", chest_png(), "image/png")})
    assert response.status_code == 503
    assert "THORAX_PACKAGES" in response.json()["detail"]
    health = client.get("/api/v1/thorax/health").json()
    assert health["models_loaded"] is False
    assert "THORAX_PACKAGES" in health["models_error"]


def test_analyze_returns_same_format_as_brain(client, detector):
    response = client.post("/api/v1/thorax/analyze/screen-roi",
                           files={"image": ("roi.png", chest_png(), "image/png")})
    assert response.status_code == 200
    body = response.json()
    cls = body["classification"]
    assert cls["task"] == "thorax"
    assert [f["label"] for f in cls["findings"]] == ["airspace_opacity", "pleural_effusion"]
    assert cls["top_label_ko"] == "폐음영" and cls["confidence"] == pytest.approx(0.42)
    assert body["germinoma"] is None
    assert body["overlay_png_base64"]
    # 화면 왼쪽 위 = 환자 우측 상부, 오른쪽 아래 = 환자 좌측 하부
    assert "폐음영 (신뢰도 0.42, 상부-환자 우측, 박스 1개)" in body["overlay_summary"]
    assert "흉막삼출 (신뢰도 0.31, 하부-환자 좌측, 박스 1개)" in body["overlay_summary"]
    assert body["disclaimer"] == constants.DISCLAIMER


def test_no_boxes_means_no_highlight(client, monkeypatch):
    service = thorax.ThoraxDetector()
    service.use_models(fake_models())
    monkeypatch.setattr(thorax, "detector_service", service)
    body = client.post("/api/v1/thorax/analyze/screen-roi",
                       files={"image": ("roi.png", chest_png(), "image/png")}).json()
    assert body["overlay_summary"] == "강조된 이상 부위 없음"
    assert all(f["score"] == 0 for f in body["classification"]["findings"])


def test_too_large_image_is_rejected(client, detector, monkeypatch):
    monkeypatch.setattr(thorax_config, "MAX_UPLOAD_BYTES", 100)
    response = client.post("/api/v1/thorax/analyze/screen-roi",
                           files={"image": ("roi.png", chest_png(), "image/png")})
    assert response.status_code == 413


def grade(**kwargs):
    return roiGradeServices.grade_image_roi(chest_png(), "roi.png", roi_type="box",
                                            st_part="2", st_modal="1", normalized=False, **kwargs)


def test_roi_on_detected_box_is_correct(detector):
    result = grade(x=20, y=20, width=60, height=60)  # 폐음영 박스와 같은 자리
    assert result["result"] == "정답"
    assert result["classification"]["task"] == "thorax"
    assert result["germinoma"] is None
    assert result["abnormality_bbox"]["x_px"] == 20
    assert result["_review_png"].startswith(b"\x89PNG")


def test_roi_far_from_boxes_is_wrong(detector):
    result = grade(x=150, y=10, width=30, height=30)  # 박스 없는 오른쪽 위
    assert result["result"] == "오답"


def test_roi_when_model_finds_nothing_is_wrong(monkeypatch):
    service = thorax.ThoraxDetector()
    service.use_models(fake_models())
    monkeypatch.setattr(thorax, "detector_service", service)
    result = grade(x=20, y=20, width=60, height=60)
    assert result["has_abnormality"] is False
    assert result["result"] == "오답"


def test_roi_grade_rejects_chest_ct():
    with pytest.raises(ValueError, match="X-ray"):
        roiGradeServices.grade_image_roi(chest_png(), "roi.png", roi_type="box",
                                         x=0, y=0, width=1, height=1, st_part="2", st_modal="2")


def test_roi_grade_other_parts_still_use_brain(monkeypatch):
    try:  # 뇌 CT 패키지는 torch·albumentations 가 있어야 불러올 수 있다
        import_module("class.brain")
    except Exception:
        pytest.skip("뇌 CT 분석 의존성이 없는 환경")

    def brain_called():
        raise RuntimeError("brain path")

    monkeypatch.setattr(roiGradeServices, "load_brain", brain_called)
    for part in (None, "1"):
        with pytest.raises(RuntimeError, match="brain path"):
            roiGradeServices.grade_image_roi(chest_png(), "roi.png", roi_type="box",
                                             x=0, y=0, width=1, height=1, st_part=part)


def test_grade_target_prefers_study_row_then_form():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    StudyModel.__table__.create(engine)
    with Session(engine) as db:
        db.add(StudyModel(idx=1, st_part="2", st_modal="1", st_image="/a.png", st_disease="기흉"))
        db.commit()
        resolve = learningServices.resolve_roi_grade_target
        assert resolve(db, study_idx=1, st_part="brain", st_modal="ct") == ("2", "1")
        assert resolve(db, study_idx=None, st_part="chest", st_modal="xray") == ("2", "1")
        assert resolve(db, study_idx=None) == (None, None)
        # 모르는 값은 None → 예전처럼 뇌 CT 채점
        assert resolve(db, study_idx=None, st_part="other", st_modal="pet") == (None, None)
    engine.dispose()


REAL_DIR = os.environ.get("THORAX_TEST_LOCAL_DIR", "")


@pytest.mark.skipif(not REAL_DIR, reason="THORAX_TEST_LOCAL_DIR 이 없으면 실제 모델 테스트를 건너뛴다")
def test_real_packages_load_and_run(monkeypatch):
    pytest.importorskip("ultralytics")
    packages = sorted(p.name for p in Path(REAL_DIR).iterdir() if p.is_dir())
    monkeypatch.setattr(thorax_config, "LOCAL_DIR", REAL_DIR)
    monkeypatch.setattr(thorax_config, "PACKAGES", packages)
    service = thorax.ThoraxDetector()
    service.load()
    assert len(service.models) == len(packages)
    # 설정 파일의 이름을 썼는지 (흉막삼출 모델 안 이름은 'item')
    assert "item" not in service.classes
    img = thorax.Preprocess.load_image_uint8_from_bytes(chest_png(512), "roi.png")
    detections = service.detect(img)
    assert all(d.finding in service.classes for d in detections)
    for model in service.models:
        settings = json.loads((service._package_dir(model.name) / "inference_config.json").read_text(encoding="utf-8"))
        assert model.confidence == pytest.approx(settings["confidence_threshold"])
