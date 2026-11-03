"""부위 자동 판별 틀: 모델이 없으면 경고하지 않고, 확신하면서 고른 부위와 다르면 경고한다."""
import io
from importlib import import_module

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from routers.bodyPart import router
from services import bodyPartServices

bodypart = import_module("class.bodypart")


def png_bytes(size: int = 64) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.full((size, size), 120, dtype=np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/body-part")
    return TestClient(app)


@pytest.fixture(autouse=True)
def fresh_classifier(monkeypatch):
    """테스트마다 모델 없는 새 판별기를 쓴다."""
    monkeypatch.setattr(bodypart, "classifier_service", bodypart.BodyPartClassifier())


def post(client, selected="brain", data=None):
    files = {"image": ("a.png", data or png_bytes(), "image/png")}
    return client.post("/api/v1/body-part/check", files=files, data={"selected": selected})


def test_without_model_answers_unknown_and_never_warns(client):
    body = post(client).json()
    assert body["label"] == "unknown" and body["model_ready"] is False
    assert body["confidence"] == 0.0 and body["mismatch"] is False and body["message"] is None
    assert body["expected"] == "brain_ct"


def test_confident_other_part_warns(client):
    bodypart.classifier_service.use_model(lambda img: {"thorax_xray": 0.95, "brain_ct": 0.03})
    body = post(client, selected="brain").json()
    assert body["label"] == "thorax_xray" and body["mismatch"] is True
    assert "뇌 CT" in body["message"] and "흉부 X-ray" in body["message"]


def test_same_part_or_low_confidence_does_not_warn(client):
    bodypart.classifier_service.use_model(lambda img: {"brain_ct": 0.97})
    assert post(client, selected="brain").json()["mismatch"] is False
    bodypart.classifier_service.use_model(lambda img: {"thorax_xray": 0.5, "brain_ct": 0.4})
    assert post(client, selected="brain").json()["mismatch"] is False    # 확신이 낮으면 통과


def test_unknown_dropdown_value_does_not_warn(client):
    bodypart.classifier_service.use_model(lambda img: {"thorax_xray": 0.99})
    body = post(client, selected="knee").json()
    assert body["expected"] is None and body["mismatch"] is False


def test_bad_images_are_rejected(client):
    assert post(client, data=b"x" * 10).status_code == 400
    assert post(client, data=b"not an image at all " * 10).status_code == 400


def test_health_reports_not_ready_without_weights(client):
    body = client.get("/api/v1/body-part/health").json()
    assert body["model_ready"] is False and body["min_confidence"] == pytest.approx(0.8)
    assert bodyPartServices.health()["model_ready"] is False
