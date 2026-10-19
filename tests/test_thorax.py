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


def test_summary_lists_each_box_by_confidence():
    """위양성 박스가 진짜 병변보다 점수가 높아도 두 위치를 모두 알려 준다."""
    img = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
    dets = [thorax.Detection("pleural_effusion", 0.28, (5, 170, 20, 190)),
            thorax.Detection("pleural_effusion", 0.08, (120, 70, 190, 150))]
    _, per_class = thorax.Localization.locate_abnormality(dets, img)
    summary = thorax.Localization.summarize_targets(per_class, classes=["pleural_effusion"])
    assert summary == "흉막삼출: 하부-환자 우측 0.28, 중부-환자 좌측 0.08"


def test_overlapping_boxes_merge_and_only_top_regions_remain():
    """같은 자리에 겹친 박스는 하나로, 소견마다 점수 높은 2곳만 남는다."""
    img = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
    dets = [thorax.Detection("airspace_opacity", 0.27, (10, 40, 60, 120)),
            thorax.Detection("airspace_opacity", 0.19, (20, 50, 70, 130)),   # 위와 겹침
            thorax.Detection("airspace_opacity", 0.11, (65, 125, 80, 140)),  # 바로 위 박스와 겹침 → 한 덩어리
            thorax.Detection("airspace_opacity", 0.05, (130, 90, 180, 140)),
            thorax.Detection("airspace_opacity", 0.03, (150, 10, 170, 30))]  # 3번째 영역 → 버림
    mask, per_class = thorax.Localization.locate_abnormality(dets, img)
    assert per_class["airspace_opacity"]["boxes"] == [((10, 40, 80, 140), 0.27), ((130, 90, 180, 140), 0.05)]
    assert not mask[20, 160]                       # 버린 박스는 채점에도 안 쓴다
    assert mask[135, 12]                           # 합친 박스가 감싸는 자리
    summary = thorax.Localization.summarize_targets(per_class, classes=["airspace_opacity"])
    assert summary.count(",") == 1


def test_small_boxes_are_dropped_by_minimum_area_ratio():
    """v2.3 설정처럼 minimum_area_ratio 가 있으면 이미지 면적 대비 작은 박스를 버린다."""
    model = thorax.FindingModel(
        "effusion", {"class_names": {"0": "Pleural effusion"}, "confidence_threshold": 0.1,
                     "minimum_area_ratio": 0.003},
        FakePredictor([((0, 0, 10, 10), 0.9, 0),        # 100px / 40000 = 0.0025 → 버림
                       ((0, 0, 20, 20), 0.2, 0)]))      # 400px / 40000 = 0.01 → 남김
    detections = model.detect(np.zeros((SIZE, SIZE, 3), dtype=np.uint8))
    assert [d.confidence for d in detections] == [0.2]


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
    assert body["overlay_summary"] == "폐음영: 상부-환자 우측 0.42; 흉막삼출: 하부-환자 좌측 0.31"
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


def box(x, y, w, h):
    return {"roi_type": "box", "x": x, "y": y, "width": w, "height": h}


def test_all_regions_found_with_several_rois_is_correct(detector):
    result = grade(rois=[box(20, 20, 60, 60), box(120, 130, 70, 60)])
    assert result["result"] == "정답"
    assert result["found_count"] == 2 and result["too_wide"] is False


def test_roi_between_two_regions_is_wrong(detector):
    """예전에는 두 부위 중심을 합쳐 가운데가 '가깝다'고 부분정답이 됐다."""
    result = grade(rois=[box(85, 85, 30, 30)])
    assert result["result"] == "오답"
    assert result["found_count"] == 0


def test_one_huge_box_over_everything_is_partial(detector):
    """다 덮어도 그린 영역 대부분이 정상이면 '너무 넓게 그림'으로 부분정답."""
    result = grade(rois=[box(0, 0, 200, 200)])
    assert result["found_count"] == 2
    assert result["too_wide"] is True
    assert result["result"] == "부분정답"


def test_answer_boxes_replace_ai_as_grading_target(detector):
    """데이터셋 정답 박스가 있으면 AI 박스 대신 그걸로 채점한다. AI 결과는 참고로만 남는다."""
    answer = [{"x": 0.0, "y": 0.6, "width": 0.3, "height": 0.3, "finding": "기흉"}]   # 왼쪽 아래, AI 박스와 다른 곳
    on_answer = grade(x=0, y=120, width=60, height=60, answer_boxes=answer)
    assert on_answer["result"] == "정답"
    assert on_answer["target_source"] == "dataset"
    assert on_answer["targets"][0]["findings"] == ["기흉"] and on_answer["targets"][0]["confidence"] is None
    assert on_answer["abnormality_bbox"]["y_px"] == 120
    assert on_answer["overlay_summary"].startswith("AI 참고")
    on_ai_box = grade(x=20, y=20, width=60, height=60, answer_boxes=answer)   # AI 폐음영 자리
    assert on_ai_box["result"] == "오답"


def test_without_answer_boxes_ai_is_the_target(detector):
    result = grade(x=20, y=20, width=60, height=60)
    assert result["target_source"] == "ai"
    assert result["targets"][0]["confidence"] == pytest.approx(0.42)


def test_overlapping_findings_count_as_one_region(monkeypatch):
    """같은 자리의 폐음영·흉막삼출은 학습자에게 한 곳이다."""
    service = thorax.ThoraxDetector()
    service.use_models(fake_models(opacity_rows=[((20, 20, 80, 80), 0.4, 0)],
                                   effusion_rows=[((50, 50, 100, 100), 0.3, 0)]))
    monkeypatch.setattr(thorax, "detector_service", service)
    result = grade(x=20, y=20, width=80, height=80)
    assert result["target_count"] == 1
    assert result["targets"][0]["findings"] == ["폐음영", "흉막삼출"]
    assert result["result"] == "정답"


@pytest.mark.parametrize("raw,word", [
    ("not json", "JSON"),
    ("[]", "하나 이상"),
    ('[{"roi_type": "box", "x": 0.1}]', "box"),
    ('[{"roi_type": "star", "x": 0.1}]', "box"),
    (json.dumps([box(0, 0, 0.1, 0.1)] * 11), "최대 10개"),
])
def test_bad_rois_are_rejected(raw, word):
    from schemas.roiGradeSchemas import parse_rois
    with pytest.raises(ValueError, match=word):
        parse_rois(raw)


def test_rois_parse_box_and_circle():
    from schemas.roiGradeSchemas import parse_rois
    assert parse_rois(None) is None and parse_rois("  ") is None
    circle = {"roi_type": "circle", "cx": 0.5, "cy": 0.5, "radius": 0.1}
    rois = parse_rois(json.dumps([box(0.1, 0.2, 0.3, 0.4), circle]))
    assert [r["roi_type"] for r in rois] == ["box", "circle"]


def test_one_of_two_regions_is_partial(detector):
    """의심 부위가 2곳(왼쪽 위 폐음영, 오른쪽 아래 흉막삼출)인데 한 곳만 그리면 부분정답."""
    result = grade(x=20, y=20, width=60, height=60)  # 폐음영 박스와 같은 자리
    assert result["result"] == "부분정답"
    assert (result["found_count"], result["target_count"]) == (1, 2)
    assert [t["found"] for t in result["targets"]] == [True, False]   # 신뢰도 순 (0.42, 0.31)
    assert result["targets"][0]["findings"] == ["폐음영"]
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
    # inference_config.json 이 있는 폴더만 패키지로 본다 (zip 을 풀면 한 단계 더 들어갈 수 있다)
    packages = sorted(p.name for p in Path(REAL_DIR).iterdir()
                      if (p / "inference_config.json").is_file() or (p / p.name / "inference_config.json").is_file())
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


def test_learning_note_for_chest_answer():
    note = thorax.learning_note("흉막삼출")
    assert note["code"] == "pleural_effusion" and note["name"] == "흉막삼출"
    assert note["kind"] == "finding" and note["appearance"] and note["related"] and note["confirm"] and note["references"]
    assert note["locations"] == []   # 의심 부위를 안 주면 위치는 비어 있다
    # 흉막삼출이 들어간 조합만 나온다
    assert any("심부전" in text for text in note["combinations"])
    assert all("기흉" not in text or "수기흉" in text for text in note["combinations"])
    assert "진단이 아닙니다" in note["disclaimer"]
    assert thorax.learning_note("Pleural effusion")["code"] == "pleural_effusion"


def test_learning_note_unknown_or_brain_is_none():
    assert thorax.learning_note(None) is None
    assert thorax.learning_note("폐결절") is None
    from services import learningNoteServices

    assert learningNoteServices.get_learning_note("1", "흉막삼출") is None
    note = learningNoteServices.get_learning_note("2", "기흉")
    assert note["code"] == "pneumothorax" and note["source"] == "static"


def test_learning_note_prefers_rag_and_fills_missing_keys(monkeypatch):
    from services import learningNoteServices

    # 의학용어 사전(RAG)이 연결되면 그쪽이 먼저다. 빠진 키는 빈 값으로 채운다
    monkeypatch.setattr(learningNoteServices, "_from_rag",
                        lambda part, disease, targets: {"name": disease, "summary": "RAG 설명"})
    note = learningNoteServices.get_learning_note("1", "경막하출혈")
    assert note["source"] == "rag" and note["summary"] == "RAG 설명"
    assert note["related"] == [] and note["pitfall"] == "" and note["references"] == []
    assert note["kind"] == "disease" and note["appearance"] == [] and note["locations"] == []   # 뇌 문제의 정답은 병명


def test_learning_note_fills_location_from_targets():
    from services import learningNoteServices

    # 채점 응답의 의심 부위(0~1 비율). 화면 왼쪽 아래 = 환자 우측 하부, 같은 자리 두 개는 한 번만
    targets = [{"x": 0.1, "y": 0.7, "width": 0.2, "height": 0.2},
               {"x": 0.12, "y": 0.72, "width": 0.1, "height": 0.1},
               {"x": 0.7, "y": 0.1, "width": 0.2, "height": 0.15}]
    note = learningNoteServices.get_learning_note("2", "흉막삼출", targets)
    assert note["kind"] == "finding" and note["source"] == "static"
    assert note["locations"] == ["하부-환자 우측", "상부-환자 좌측"]


def test_case_summary_describes_this_image_from_targets():
    rf = import_module("class.thorax.report_format")
    one = rf.case_summary("흉막삼출", [{"x": 0.1, "y": 0.7, "width": 0.2, "height": 0.2}])
    assert one == "이 영상에서는 흉막삼출 의심 부위가 1곳 있습니다: 하부-환자 우측(중간 범위). 한쪽에만 있습니다."
    both = rf.case_summary("흉막삼출", [{"x": 0.1, "y": 0.7, "width": 0.2, "height": 0.2},
                                    {"x": 0.7, "y": 0.7, "width": 0.1, "height": 0.1}])
    assert "2곳" in both and both.endswith("양쪽에 있습니다.")
    assert rf.case_summary("기흉", []) == ""
    note = thorax.learning_note("흉막삼출", [{"x": 0.1, "y": 0.7, "width": 0.2, "height": 0.2}])
    assert note["case_summary"].startswith("이 영상에서는 흉막삼출")


def test_glossary_definition_replaces_summary(tmp_path, monkeypatch):
    from services import learningNoteServices

    csv_path = tmp_path / "glossary.csv"
    csv_path.write_text("en,kr,definition,definition_kr\n"
                        "pleural effusion,흉막삼출,x,흉막강 내에 액체가 존재하는 상태. 질병의 징후이다. 세 번째 문장.\n",
                        encoding="utf-8")
    monkeypatch.setattr(learningNoteServices.settings, "glossary_csv_path", str(csv_path))
    learningNoteServices._glossary.cache_clear()
    note = learningNoteServices.get_learning_note("2", "흉막삼출")
    assert note["summary_source"] == "glossary"
    assert note["summary"] == "흉막강 내에 액체가 존재하는 상태. 질병의 징후이다."   # 두 문장까지
    # 사전에 없는 용어는 고정 해설 그대로
    assert learningNoteServices.get_learning_note("2", "폐음영")["summary_source"] == "static"
    learningNoteServices._glossary.cache_clear()


def test_roi_feedback_explains_why():
    W = H = 100

    def m(x1, y1, x2, y2):
        a = np.zeros((H, W), dtype=bool)
        a[y1:y2, x1:x2] = True
        return a

    target = [(10, 60, 40, 90)]   # 화면 왼쪽 아래 = 환자 우측 하부
    fb = roiGradeServices.roi_feedback
    assert "좌우를 반대로" in fb([m(65, 60, 95, 90)], target, W, H, chest=True)[0]
    assert "일부만" in fb([m(15, 65, 25, 75)], target, W, H, chest=True)[0]
    assert "더 아래쪽" in fb([m(10, 10, 40, 40)], target, W, H, chest=True)[0]
    assert fb([m(10, 60, 40, 90), m(60, 10, 80, 30)], target, W, H, found=[True], chest=True) == [
        "정답 부위가 아닌 곳에 그린 ROI 가 1개 있어요."]
    assert "너무 넓게" in fb([m(0, 0, 100, 100)], target, W, H, found=[True], too_wide=True)[0]
    assert fb([], target, W, H) == ["ROI 를 그리지 않았습니다."]
    assert fb([m(10, 60, 40, 90)], target, W, H, found=[True]) == []
