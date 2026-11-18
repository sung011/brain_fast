"""복부 CT 장기 검출(class/abdomen)·API·판독문을 검증한다.

대부분은 정해진 박스를 돌려주는 가짜 탐지 모델로 확인한다 (torch·ultralytics 불필요).
"""
import io
import json
from importlib import import_module

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

abdomen = import_module("class.abdomen")
constants = import_module("class.abdomen.constants")
abdomen_config = import_module("class.abdomen.config")
report_format = import_module("class.abdomen.report_format")

from routers.abdomen import router  # noqa: E402

SIZE = 200


def abdomen_png(size: int = SIZE) -> bytes:
    img = Image.new("L", (size, size), 30)
    draw = ImageDraw.Draw(img)
    draw.ellipse([40, 40, 120, 120], fill=160)
    draw.rectangle([130, 80, 180, 150], fill=120)
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
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def predict(self, img, **kwargs):
        self.calls.append(kwargs)
        return [type("Result", (), {"boxes": _Boxes(self.rows)})()]


@pytest.fixture
def detector(monkeypatch):
    service = abdomen.AbdomenDetector()
    # liver=7, kidney=6 (constants.CLASSES 순서)
    service.use_predictor(
        FakePredictor([
            ((20, 20, 90, 90), 0.72, 7),
            ((110, 100, 180, 170), 0.41, 6),
        ]),
        names={i: name for i, name in enumerate(constants.CLASSES)},
    )
    monkeypatch.setattr(abdomen, "detector_service", service)
    return service


@pytest.fixture
def client(detector):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/abdomen")
    with TestClient(app) as c:
        yield c


def test_class_labels():
    assert constants.class_ko("liver") == "간"
    assert constants.class_ko("pancreatic") == "췌장"
    assert len(constants.CLASSES) == 13
    assert all(name in constants.CLASS_KO for name in constants.CLASSES)


def test_png_becomes_three_channel_gray():
    img = abdomen.Preprocess.load_image_uint8_from_bytes(abdomen_png(), "roi.png")
    assert img.shape == (SIZE, SIZE, 3) and img.dtype == np.uint8
    assert (img[..., 0] == img[..., 1]).all()


def test_detect_and_scores(detector):
    img = abdomen.Preprocess.load_image_uint8_from_bytes(abdomen_png(), "roi.png")
    dets = detector.detect(img)
    assert len(dets) == 2
    assert dets[0].finding == "liver" and dets[0].confidence == pytest.approx(0.72)
    scores = detector.scores(dets)
    assert scores[constants.CLASSES.index("liver")] == pytest.approx(0.72)
    assert scores[constants.CLASSES.index("kidney")] == pytest.approx(0.41)
    assert scores[constants.CLASSES.index("spleen")] == 0


def test_overlay_and_summary(detector):
    img = abdomen.Preprocess.load_image_uint8_from_bytes(abdomen_png(), "roi.png")
    dets = detector.detect(img)
    _, per_class = abdomen.Localization.locate_abnormality(dets, img)
    assert "liver" in per_class and "kidney" in per_class
    summary = abdomen.Localization.summarize_targets(per_class, classes=list(constants.CLASSES))
    assert "간" in summary and "신장" in summary
    overlay = abdomen.Localization.build_overlay_image(img, per_class)
    assert overlay.shape == img.shape
    b64 = abdomen.Localization.overlay_to_base64(overlay)
    assert len(b64) > 100


def test_template_report():
    probs = [0.0] * 13
    probs[constants.CLASSES.index("liver")] = 0.8
    probs[constants.CLASSES.index("kidney")] = 0.4
    per_class = {
        "liver": {
            "prob": 0.8,
            "mask": np.zeros((100, 100), dtype=bool),
            "boxes": [((10, 10, 40, 40), 0.8)],
        }
    }
    per_class["liver"]["mask"][10:40, 10:40] = True
    report = report_format.build_template_report(probs, per_class)
    assert "【검사】" in report and "【소견】" in report and "간" in report
    merged = report_format.merge_ai_reference(report, probs)
    assert "【AI참고】" in merged and "【면책】" in merged


def test_health_and_analyze(client):
    health = client.get("/api/v1/abdomen/health").json()
    assert health["models_loaded"] is True
    assert health["status"] == "ok"

    response = client.post(
        "/api/v1/abdomen/analyze/screen-roi",
        files={"image": ("roi.png", abdomen_png(), "image/png")},
        data={"modality": "ct", "include_overlay": "true"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["classification"]["task"] == "abdomen"
    assert body["classification"]["top_label"] == "liver"
    assert body["classification"]["top_label_ko"] == "간"
    assert len(body["classification"]["probs"]) == 13
    assert body["overlay_png_base64"]
    assert "간" in (body["overlay_summary"] or "")


def test_explain(client, monkeypatch):
    # template 백엔드로 고정
    monkeypatch.setattr(abdomen_config, "MEDGEMMA_BACKEND", "template")
    svc = abdomen.MedGemmaService()
    svc.backend = "template"
    svc.load()
    monkeypatch.setattr(abdomen, "medgemma_service", svc)

    probs = [0.0] * 13
    probs[constants.CLASSES.index("liver")] = 0.72
    probs[constants.CLASSES.index("kidney")] = 0.41
    response = client.post(
        "/api/v1/abdomen/analyze/screen-roi/explain",
        files={"image": ("roi.png", abdomen_png(), "image/png")},
        data={"probs_json": json.dumps(probs)},
    )
    assert response.status_code == 200
    body = response.json()
    assert "【소견】" in body["explanation"]
    assert "간" in body["explanation"]


def test_analyze_rejects_tiny_image(client):
    response = client.post(
        "/api/v1/abdomen/analyze/screen-roi",
        files={"image": ("roi.png", b"tiny", "image/png")},
    )
    assert response.status_code == 400


def test_explain_rejects_bad_probs(client):
    response = client.post(
        "/api/v1/abdomen/analyze/screen-roi/explain",
        files={"image": ("roi.png", abdomen_png(), "image/png")},
        data={"probs_json": "[0.1, 0.2]"},
    )
    assert response.status_code == 400


def test_not_configured_returns_503(monkeypatch, client):
    monkeypatch.setattr(abdomen_config, "MODEL_PATH", "")
    monkeypatch.setattr(abdomen_config, "hf_repo", lambda: "")
    service = abdomen.AbdomenDetector()
    monkeypatch.setattr(abdomen, "detector_service", service)
    # model_configured 가 False 가 되도록
    monkeypatch.setattr(abdomen_config, "model_configured", lambda: False)
    response = client.post(
        "/api/v1/abdomen/analyze/screen-roi",
        files={"image": ("roi.png", abdomen_png(), "image/png")},
    )
    assert response.status_code == 503


def grade(**kwargs):
    from services import roiGradeServices

    return roiGradeServices.grade_image_roi(
        abdomen_png(),
        "roi.png",
        roi_type="box",
        st_part="3",
        st_modal="2",
        normalized=False,
        **kwargs,
    )


def test_roi_grade_saves_recall_iou_fields(detector, monkeypatch):
    """복부 채점은 포함률(recall)·iou 를 돌려주고, 그 값이 review_node 저장에 쓰인다."""
    from services import reviewNodeServices, roiGradeServices

    result = grade(x=20, y=20, width=70, height=70)
    assert result["classification"]["task"] == "abdomen"
    assert result["has_abnormality"] is True
    assert "recall" in result and "iou" in result
    assert result["recall"] >= 0
    assert result["_review_png"].startswith(b"\x89PNG")

    # save_submission 이 grade 의 recall/iou 를 rn_* 로 넘기는지 (DB·스토리지 없이 create 만 감시)
    captured = {}

    class _Row:
        idx = 99
        rn_image = None
        rn_solution = "C"

    def fake_create(db, **kwargs):
        captured.update(kwargs)
        return _Row()

    monkeypatch.setattr(reviewNodeServices, "storage_service", type("S", (), {"enabled": False})())
    monkeypatch.setattr(reviewNodeServices.repositories, "create", fake_create)

    import asyncio

    saved = asyncio.run(
        reviewNodeServices.save_submission(
            db=None,
            image_bytes=abdomen_png(),
            image_filename="roi.png",
            grade=result,
            study_idx=None,  # 무릎 차단 SELECT 를 건너뛴다
            user_idx=2,
        )
    )
    assert saved["review_idx"] == 99
    assert captured["rn_recall"] == pytest.approx(result["recall"])
    assert captured["rn_iou"] == pytest.approx(result["iou"])
    assert captured["rn_overlay_summary"]
    assert captured["rn_findings"] is not None


def test_roi_grade_rejects_abdomen_xray(detector):
    from services import roiGradeServices

    with pytest.raises(ValueError, match="X-ray"):
        roiGradeServices.grade_image_roi(
            abdomen_png(),
            "roi.png",
            roi_type="box",
            x=0,
            y=0,
            width=1,
            height=1,
            st_part="3",
            st_modal="1",
        )


def test_roi_grade_abdomen_mri_also_grades(detector):
    """복부 MRI 문제도 채점·저장 경로를 탄다(모달 때문에 400 나지 않음)."""
    from services import roiGradeServices

    result = roiGradeServices.grade_image_roi(
        abdomen_png(),
        "roi.png",
        roi_type="box",
        x=20,
        y=20,
        width=70,
        height=70,
        st_part="abdomen",
        st_modal="mri",
        normalized=False,
    )
    assert result["classification"]["task"] == "abdomen"
    assert "recall" in result and "iou" in result


def test_roi_grade_abdomen_part_not_brain(detector, monkeypatch):
    from services import roiGradeServices

    def brain_called():
        raise RuntimeError("brain path")

    monkeypatch.setattr(roiGradeServices, "load_brain", brain_called)
    result = grade(x=20, y=20, width=70, height=70)
    assert result["classification"]["task"] == "abdomen"
    assert result["result"] in ("정답", "부분정답", "오답")


def test_learning_roi_grade_route_saves_review_node(detector, monkeypatch):
    """POST /learning/roi-grade + 복부 문제 → review_node 에 포함률·iou 가 저장된다."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from db import Base, get_db
    from models.reviewNodeModel import ReviewNodeModel
    from models.studyModel import StudyModel
    from routers import commonness
    from services import objectStorageServices as storage_mod

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[StudyModel.__table__, ReviewNodeModel.__table__])
    Session = sessionmaker(bind=engine, autoflush=False)
    with Session() as db:
        row = StudyModel(
            st_part="3",
            st_modal="2",
            st_image="/stylesheets/assets/abdomen/ct.png",
            st_disease="간",
            del_yn="N",
            state="N",
        )
        db.add(row)
        db.commit()
        study_idx = row.idx

    def override_db():
        with Session() as db:
            yield db

    app = FastAPI()
    app.include_router(commonness.router)
    app.dependency_overrides[get_db] = override_db

    monkeypatch.setattr(
        storage_mod,
        "storage_service",
        type("S", (), {"enabled": False})(),
    )

    with TestClient(app) as client:
        res = client.post(
            "/learning/roi-grade",
            data={
                "roi_type": "box",
                "x": "20",
                "y": "20",
                "width": "70",
                "height": "70",
                "normalized": "false",
                "study_idx": str(study_idx),
                "user_idx": "7",
                "st_part": "abdomen",
                "st_modal": "ct",
            },
            files={"image": ("roi.png", abdomen_png(), "image/png")},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["classification"]["task"] == "abdomen"
    assert body["review_idx"] is not None
    assert body["recall"] is not None
    assert body["iou"] is not None

    with Session() as db:
        saved = db.execute(
            select(ReviewNodeModel).where(ReviewNodeModel.idx == body["review_idx"])
        ).scalar_one()
        assert saved.rn_s_idx == study_idx
        assert saved.rn_u_idx == 7
        assert saved.rn_recall is not None
        assert saved.rn_iou is not None
        assert saved.rn_solution in ("C", "H", "W")
        assert saved.rn_findings  # 장기 소견 JSON

    engine.dispose()
