"""흉부 X-ray 분석(class/thorax)·API·ROI 채점 분기를 가짜 모델로 검증한다.

실제 팀 가중치 대신 무작위 초기화한 resnet18 체크포인트를 임시 폴더에 만들어 쓴다.
torch·timm 이 없는 환경에서는 건너뛴다.
"""
import io
from importlib import import_module

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

torch = pytest.importorskip("torch")
timm = pytest.importorskip("timm")
pytest.importorskip("albumentations")

thorax = import_module("class.thorax")
constants = import_module("class.thorax.constants")
thorax_config = import_module("class.thorax.config")
ensemble_mod = import_module("class.thorax.ensemble")

from models.studyModel import StudyModel  # noqa: E402
from routers.thorax import router  # noqa: E402
from services import learningServices, roiGradeServices  # noqa: E402


def chest_png(size: int = 160) -> bytes:
    """검은 배경 위에 흉곽과 두 폐야를 흉내 낸 흑백 PNG."""
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)
    draw.rectangle([12, 12, size - 12, size - 12], fill=170)
    draw.ellipse([25, 25, size // 2 - 5, size - 30], fill=60)
    draw.ellipse([size // 2 + 5, 25, size - 25, size - 30], fill=60)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_ckpt(path, num_classes: int, classes=None) -> None:
    torch.manual_seed(0)
    model = timm.create_model("resnet18", pretrained=False, num_classes=num_classes)
    blob = {"model_state_dict": model.state_dict(), "model_name": "resnet18"}
    if classes is not None:
        blob["classes"] = classes
    torch.save(blob, path)


@pytest.fixture
def fake_service(tmp_path, monkeypatch):
    """가짜 체크포인트로 로드한 앙상블을 class.thorax.ensemble_service 자리에 넣는다."""
    ckpt = tmp_path / "best_cxr_resnet18.pt"
    make_ckpt(ckpt, len(constants.CLASSES), constants.CLASSES)
    service = thorax.ThoraxEnsemble(ckpt_files=[ckpt.name])
    service.load_paths([ckpt])
    monkeypatch.setattr(thorax, "ensemble_service", service)
    return service


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/thorax")
    with TestClient(app) as c:
        yield c


def test_labels_support_languages_and_fallback():
    assert constants.class_label("Pneumothorax") == "기흉"
    assert constants.class_label("Pneumothorax", "en") == "Pneumothorax"
    # 아직 없는 언어는 한국어로, 모르는 클래스는 원문으로
    assert constants.class_label("Pneumothorax", "ja") == "기흉"
    assert constants.class_label("Unknown finding") == "Unknown finding"
    assert all(name in constants.LABELS for name in constants.CLASSES)


def test_png_becomes_three_channel_gray():
    img = thorax.Preprocess.load_image_uint8_from_bytes(chest_png(), "roi.png")
    assert img.shape == (160, 160, 3) and img.dtype == np.uint8
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


def test_without_model_settings_api_answers_503(client, monkeypatch):
    monkeypatch.setattr(thorax_config, "HF_REPO", "")
    monkeypatch.setattr(thorax, "ensemble_service", thorax.ThoraxEnsemble(ckpt_files=[]))
    response = client.post("/api/v1/thorax/analyze/screen-roi",
                           files={"image": ("roi.png", chest_png(), "image/png")})
    assert response.status_code == 503
    assert "THORAX_HF_REPO" in response.json()["detail"]
    health = client.get("/api/v1/thorax/health").json()
    assert health["models_loaded"] is False
    assert "THORAX_HF_REPO" in health["models_error"]


def test_class_count_must_match_checkpoint(tmp_path):
    ckpt = tmp_path / "best_cxr_resnet18.pt"
    make_ckpt(ckpt, 3)  # classes 목록 없이 출력 3개
    service = thorax.ThoraxEnsemble(ckpt_files=[ckpt.name])
    with pytest.raises(ValueError, match="클래스 수"):
        service.load_paths([ckpt])


def test_checkpoint_classes_override_default_order(tmp_path):
    ckpt = tmp_path / "best_cxr_resnet18.pt"
    classes = ["Pneumothorax", "Cardiomegaly", "Edema"]
    make_ckpt(ckpt, 3, classes)
    service = thorax.ThoraxEnsemble(ckpt_files=[ckpt.name])
    service.load_paths([ckpt])
    assert service.classes == classes


def test_analyze_returns_same_format_as_brain(client, fake_service):
    response = client.post("/api/v1/thorax/analyze/screen-roi",
                           files={"image": ("roi.png", chest_png(), "image/png")})
    assert response.status_code == 200
    body = response.json()
    cls = body["classification"]
    assert cls["task"] == "thorax"
    assert len(cls["findings"]) == len(constants.CLASSES) == len(cls["probs"])
    assert cls["top_label_ko"] == constants.class_ko(cls["top_label"])
    assert cls["findings"][0]["score"] >= cls["findings"][-1]["score"]
    assert body["germinoma"] is None
    assert body["overlay_png_base64"]
    assert body["overlay_summary"]
    assert body["roi"] == {"width": 160, "height": 160}
    assert body["disclaimer"] == constants.DISCLAIMER


def test_too_large_image_is_rejected(client, fake_service, monkeypatch):
    monkeypatch.setattr(thorax_config, "MAX_UPLOAD_BYTES", 100)
    response = client.post("/api/v1/thorax/analyze/screen-roi",
                           files={"image": ("roi.png", chest_png(), "image/png")})
    assert response.status_code == 413


def test_roi_grade_chest_xray_uses_thorax_model(fake_service):
    result = roiGradeServices.grade_image_roi(
        chest_png(), "roi.png", roi_type="box", x=0, y=0, width=1, height=1,
        st_part="2", st_modal="1",
    )
    assert result["classification"]["task"] == "thorax"
    assert result["germinoma"] is None
    assert result["has_abnormality"] is True
    # 전체를 덮는 박스는 이상 부위를 모두 포함하므로 정답
    assert result["result"] == "정답"
    assert result["_review_png"].startswith(b"\x89PNG")


def test_roi_grade_far_box_is_wrong_or_partial(fake_service):
    result = roiGradeServices.grade_image_roi(
        chest_png(), "roi.png", roi_type="box", x=0, y=0, width=0.02, height=0.02,
        st_part="2", st_modal="1",
    )
    assert result["result"] in ("오답", "부분정답")


def test_roi_grade_rejects_chest_ct():
    with pytest.raises(ValueError, match="X-ray"):
        roiGradeServices.grade_image_roi(
            chest_png(), "roi.png", roi_type="box", x=0, y=0, width=1, height=1,
            st_part="2", st_modal="2",
        )


def test_roi_grade_other_parts_still_use_brain(monkeypatch):
    def brain_called():
        raise RuntimeError("brain path")

    monkeypatch.setattr(roiGradeServices, "load_brain", brain_called)
    for part in (None, "1"):
        with pytest.raises(RuntimeError, match="brain path"):
            roiGradeServices.grade_image_roi(
                chest_png(), "roi.png", roi_type="box", x=0, y=0, width=1, height=1, st_part=part,
            )


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
