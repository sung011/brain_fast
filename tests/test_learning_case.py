"""학습자 문제 풀이 화면(/learning/case)과 문제 영상 대신 받기, 채점 후 정답 병명을 검증한다.

NAS 는 httpx.MockTransport 로 흉내 낸다. 실제 NAS·모델은 쓰지 않는다.
"""
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from db import get_db
from models.studyModel import StudyModel
from routers import commonness
from services import learningServices, reviewNodeServices, roiGradeServices

ROOT = Path(__file__).resolve().parents[1]


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("L", (32, 32), 128).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def app_client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    StudyModel.__table__.create(engine)
    with Session(engine) as db:
        db.add_all([
            StudyModel(idx=1, st_part="2", st_modal="1", st_image="/stylesheets/assets/chest/xray/폐경화_01.png",
                       st_disease="폐경화"),
            StudyModel(idx=2, st_part="1", st_modal="3",
                       st_image=json.dumps(["/mri/a.png", "/mri/b.png"]), st_disease="생식세포종양"),
            StudyModel(idx=3, del_yn="Y", st_part="2", st_modal="1", st_image="/x.png", st_disease="기흉"),
            StudyModel(idx=4, st_part="2", st_modal="1", st_image="/x.dcm", st_disease="기흉"),
        ])
        db.commit()

    requested = []

    def nas(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path.endswith("b.png"):
            return httpx.Response(404)
        return httpx.Response(200, content=png_bytes())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(learningServices.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(nas)))
    monkeypatch.setattr(learningServices.settings, "nas_public_base_url", "https://nas.example/public")

    app = FastAPI()
    app.include_router(commonness.router)
    app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")

    def test_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as client:
        yield client, requested
    engine.dispose()


def test_case_page_and_assets(app_client):
    client, _ = app_client
    page = client.get("/learning/case?part=chest&modal=xray")
    assert page.status_code == 200
    assert 'id="stage"' in page.text and 'id="submitBtn"' in page.text
    assert client.get("/assets/js/learning-case.js").status_code == 200
    assert client.get("/assets/css/learning-case.css").status_code == 200


def test_problem_image_is_fetched_from_study_path(app_client):
    client, requested = app_client
    response = client.get("/learning/problem/1/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
    # 한글 파일 이름도 헤더에 안전하게 들어간다 (RFC 5987)
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert requested == ["https://nas.example/public/stylesheets/assets/chest/xray/%ED%8F%90%EA%B2%BD%ED%99%94_01.png"]


def test_mri_slice_index_selects_image(app_client):
    client, requested = app_client
    assert client.get("/learning/problem/2/image?slice_index=0").status_code == 200
    assert requested[-1].endswith("/mri/a.png")
    assert client.get("/learning/problem/2/image?slice_index=5").status_code == 400
    assert client.get("/learning/problem/2/image?slice_index=-1").status_code == 422


@pytest.mark.parametrize("idx,status", [(3, 404), (99, 404), (4, 400)])
def test_problem_image_errors(app_client, idx, status):
    client, _ = app_client
    assert client.get(f"/learning/problem/{idx}/image").status_code == status


def test_nas_failure_is_502(app_client):
    client, _ = app_client
    assert client.get("/learning/problem/2/image?slice_index=1").status_code == 502


def test_roi_grade_reveals_expected_disease_after_submit(app_client, monkeypatch):
    client, _ = app_client
    monkeypatch.setattr(roiGradeServices, "grade_image_roi",
                        lambda *a, **k: {"ok": True, "result": "정답", "_review_png": b"x"})

    async def fake_save(db, **kwargs):
        return {"review_idx": 7, "rn_image": "/r.png", "rn_solution": "1"}

    monkeypatch.setattr(reviewNodeServices, "save_submission", fake_save)
    response = client.post("/learning/roi-grade", files={"image": ("a.png", png_bytes(), "image/png")},
                           data={"roi_type": "box", "x": 0, "y": 0, "width": 0.5, "height": 0.5, "study_idx": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["expected_disease"] == "폐경화"
    assert "_review_png" not in body
    # 문제를 받을 때는 정답을 주지 않는다
    problem = client.get("/learning/problem?st_part=chest&st_modal=xray").json()["problem"]
    assert "st_disease" not in problem
