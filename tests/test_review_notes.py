"""오답노트(목록·상세·다시 풀기)와 NAS 실패 시에도 채점 기록이 남는지 확인한다. 모델·NAS 는 쓰지 않는다."""
import asyncio
import io
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from db import get_db
from models.reviewNodeModel import ReviewNodeModel
from models.studyModel import StudyModel
from routers import commonness
from services import reviewNodeServices, roiGradeServices

NOW = datetime(2026, 10, 1, 12, 0, 0)   # 미리 넣는 기록은 과거 시각 (새 제출이 최신이 되도록)


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("L", (32, 32), 100).save(buf, format="PNG")
    return buf.getvalue()


class FakeNas:
    """NAS 대역. enabled·실패 여부를 바꿔 가며 쓴다."""

    def __init__(self, enabled=True, fail=False):
        self.enabled = enabled
        self.fail = fail
        self.uploaded = []

    async def upload_bytes(self, content, name, remote_dir=None):
        if self.fail:
            raise RuntimeError("secret-nas-detail")
        self.uploaded.append(name)
        return {"remote_path": f"/web/mu_shop/public{remote_dir}/{name}"}

    def to_public_path(self, remote_path):
        return remote_path.replace("/web/mu_shop/public", "")


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    StudyModel.__table__.create(eng)
    ReviewNodeModel.__table__.create(eng)
    with Session(eng) as db:
        db.add_all([
            StudyModel(idx=1, st_part="2", st_modal="1", st_image="/chest/a.png", st_disease="흉막삼출"),
            StudyModel(idx=2, st_part="2", st_modal="1", st_image="/chest/b.png", st_disease="폐경화"),
            StudyModel(idx=3, del_yn="Y", st_part="2", st_modal="1", st_image="/chest/c.png", st_disease="기흉"),
            StudyModel(idx=4, st_part="1", st_modal="2", st_image="/brain/d.png", st_disease="경막하출혈"),
        ])
        db.add_all([
            # 회원 7: 오답(1), 부분정답(2), 정답(4), 삭제된 문제의 오답(3)
            ReviewNodeModel(idx=10, rn_u_idx=7, rn_s_idx=1, rn_disease="흉막삼출", rn_solution="W",
                            rn_image="/stylesheets/assets/review/a.png", created_at=NOW - timedelta(hours=3)),
            ReviewNodeModel(idx=11, rn_u_idx=7, rn_s_idx=2, rn_disease="폐경화", rn_solution="H",
                            rn_image=None, created_at=NOW - timedelta(hours=2)),
            ReviewNodeModel(idx=12, rn_u_idx=7, rn_s_idx=4, rn_disease="경막하출혈", rn_solution="C",
                            created_at=NOW - timedelta(hours=1)),
            ReviewNodeModel(idx=13, rn_u_idx=7, rn_s_idx=3, rn_disease="기흉", rn_solution="W", created_at=NOW),
            # 다른 회원 8 의 오답
            ReviewNodeModel(idx=20, rn_u_idx=8, rn_s_idx=1, rn_disease="흉막삼출", rn_solution="W", created_at=NOW),
            # 지워진 기록
            ReviewNodeModel(idx=21, del_yn="Y", rn_u_idx=7, rn_s_idx=1, rn_solution="W", created_at=NOW),
        ])
        db.commit()
    yield eng
    eng.dispose()


@pytest.fixture
def client(engine, monkeypatch):
    monkeypatch.setattr(reviewNodeServices.settings, "nas_public_base_url", "https://nas.example/public")
    app = FastAPI()
    app.include_router(commonness.router)

    def test_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as c:
        yield c


def test_wrong_notes_default_to_wrong_and_partial_newest_first(client):
    body = client.get("/learning/reviews?user_idx=7").json()
    assert [i["review_idx"] for i in body["items"]] == [13, 11, 10]
    first = body["items"][-1]
    assert first["solution"] == "W" and first["solution_label"] == "오답"
    assert first["disease"] == "흉막삼출"
    assert first["st_part_label"] == "흉부" and first["st_modal_label"] == "X-ray"
    assert first["image_url"] == "https://nas.example/public/stylesheets/assets/review/a.png"
    assert body["items"][1]["image_url"] is None   # NAS 실패로 그림이 없는 기록


@pytest.mark.parametrize("solution,expected", [("all", [13, 12, 11, 10]), ("C", [12]), ("w,h", [13, 11, 10])])
def test_solution_filter(client, solution, expected):
    body = client.get(f"/learning/reviews?user_idx=7&solution={solution}").json()
    assert [i["review_idx"] for i in body["items"]] == expected


def test_bad_filter_and_missing_user(client):
    assert client.get("/learning/reviews?user_idx=7&solution=X").status_code == 400
    assert client.get("/learning/reviews").status_code == 422
    assert client.get("/learning/reviews?user_idx=7&limit=1").json()["count"] == 1


def test_detail_includes_problem_without_answer(client):
    review = client.get("/learning/reviews/10?user_idx=7").json()["review"]
    assert review["disease"] == "흉막삼출"
    assert review["problem"] == {"idx": 1, "st_part": "2", "st_modal": "1", "st_image": "/chest/a.png"}
    # 지워진 문제는 다시 풀 수 없다
    assert client.get("/learning/reviews/13?user_idx=7").json()["review"]["problem"] is None


@pytest.mark.parametrize("review_idx,user_idx", [(20, 7), (10, 8), (21, 7), (999, 7)])
def test_cannot_see_others_or_deleted(client, review_idx, user_idx):
    assert client.get(f"/learning/reviews/{review_idx}?user_idx={user_idx}").status_code == 404


def test_retry_problem_by_idx_hides_answer(client):
    body = client.get("/learning/problem/1").json()
    assert body["problem"] == {"idx": 1, "st_part": "2", "st_modal": "1", "st_image": "/chest/a.png"}
    assert "st_disease" not in body["problem"]
    assert client.get("/learning/problem/3").status_code == 404
    assert client.get("/learning/problem/999").status_code == 404


@pytest.mark.parametrize("nas,saved,error_word", [
    (FakeNas(enabled=False), False, "설정"),
    (FakeNas(fail=True), False, "업로드"),
    (FakeNas(), True, None),
])
def test_submission_is_recorded_even_if_nas_fails(engine, monkeypatch, nas, saved, error_word):
    monkeypatch.setattr(reviewNodeServices, "nas_service", nas)
    with Session(engine) as db:
        result = asyncio.run(reviewNodeServices.save_submission(
            db, image_bytes=png_bytes(), image_filename="a.png",
            grade={"result": "오답", "_review_png": png_bytes()}, study_idx=2, user_idx=7))
        row = db.get(ReviewNodeModel, result["review_idx"])
    assert row.rn_solution == "W" and row.rn_u_idx == 7 and row.rn_disease == "폐경화"
    assert result["image_saved"] is saved
    assert (row.rn_image is not None) is saved
    if error_word:
        assert error_word in result["image_error"]
        assert "secret-nas-detail" not in result["image_error"]
    else:
        assert result["image_error"] is None and row.rn_image.startswith("/stylesheets/assets/review/")


def test_roi_grade_still_answers_when_nas_is_off(client, monkeypatch):
    monkeypatch.setattr(reviewNodeServices, "nas_service", FakeNas(enabled=False))
    monkeypatch.setattr(roiGradeServices, "grade_image_roi",
                        lambda *a, **k: {"ok": True, "result": "부분정답", "_review_png": png_bytes()})
    response = client.post("/learning/roi-grade", files={"image": ("a.png", png_bytes(), "image/png")},
                           data={"roi_type": "box", "x": 0, "y": 0, "width": 0.5, "height": 0.5,
                                 "study_idx": 2, "user_idx": 7})
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "부분정답" and body["image_saved"] is False
    # 방금 제출한 기록이 오답노트에 나온다
    notes = client.get("/learning/reviews?user_idx=7").json()["items"]
    assert notes[0]["review_idx"] == body["review_idx"] and notes[0]["solution"] == "H"
