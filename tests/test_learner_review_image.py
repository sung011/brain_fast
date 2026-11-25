"""학습자 리뷰/기록 이미지 주소: 내부 /knee_series 를 노출하지 않고 problem image API 로 돌린다."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services import learningServices as learning
from services import reviewNodeServices as review_svc


def test_learner_review_image_rewrites_knee_series():
    href = learning.learner_review_image(
        study_idx=132,
        rn_image="/knee_series/case-456/slice_015.png",
        rn_slice_index=15,
        st_part="4",
        st_modal="3",
    )
    assert href == "/learning/problem/132/image?slice_index=15"
    assert "/knee_series/" not in href


def test_resolve_slice_from_filename_when_db_null():
    assert learning.resolve_review_slice_index(
        rn_slice_index=None,
        rn_image="/knee_series/case-456/slice_008.png",
    ) == 8


def test_stylesheets_review_overlay_keeps_public_url(monkeypatch):
    monkeypatch.setattr(learning.settings, "nas_public_base_url", "https://nas.example/public")
    href = learning.learner_review_image(
        study_idx=1,
        rn_image="/stylesheets/assets/review/a.png",
        st_part="2",
        st_modal="1",
    )
    assert href == "https://nas.example/public/stylesheets/assets/review/a.png"


def test_knee_review_overlay_keeps_stylesheets_url(monkeypatch):
    """무릎도 ROI 오버레이(/stylesheets/...)는 문제 API로 바꾸지 않는다."""
    monkeypatch.setattr(learning.settings, "nas_public_base_url", "https://nas.example/public")
    href = learning.learner_review_image(
        study_idx=132,
        rn_image="/stylesheets/assets/review/knee_roi.png",
        rn_slice_index=8,
        st_part="4",
        st_modal="3",
    )
    assert href == "https://nas.example/public/stylesheets/assets/review/knee_roi.png"
    assert "/knee_series/" not in href
    assert "/learning/problem/" not in href


def test_get_saved_review_hides_internal_path(monkeypatch):
    review = SimpleNamespace(
        idx=99,
        rn_s_idx=132,
        rn_disease="ACL",
        rn_solution="C",
        rn_image="/knee_series/case-456/slice_015.png",
        rn_slice_index=15,
        rn_recall=None,
        rn_iou=None,
        rn_overlay_summary=None,
        rn_findings=None,
    )
    study = SimpleNamespace(
        st_part="4",
        st_modal="3",
        st_disease="ACL",
        st_image=json.dumps(["/knee_series/case-456/slice_015.png"]),
    )
    monkeypatch.setattr(
        review_svc.repositories,
        "get_owned",
        lambda db, review_idx, user_idx: (review, study),
    )
    body = review_svc.get_saved_review(SimpleNamespace(), 99, 3)
    assert body["image"] == "/learning/problem/132/image?slice_index=15"
    assert body["slice_index"] == 15
    assert body["study_image"] == ""
    assert "/knee_series/" not in body["image"]


def test_get_saved_review_returns_user_roi(monkeypatch):
    findings = json.dumps(
        {
            "user_roi": {"type": "box", "x": 0.2, "y": 0.3, "width": 0.25, "height": 0.2},
            "user_rois": [{"type": "box", "x": 0.2, "y": 0.3, "width": 0.25, "height": 0.2}],
            "abnormality_bbox": {"x": 0.22, "y": 0.31, "width": 0.2, "height": 0.18},
        },
        ensure_ascii=False,
    )
    review = SimpleNamespace(
        idx=100,
        rn_s_idx=1,
        rn_disease="두개내출혈",
        rn_solution="H",
        rn_image="/stylesheets/assets/review/a.png",
        rn_slice_index=None,
        rn_recall=0.5,
        rn_iou=0.4,
        rn_overlay_summary="요약",
        rn_findings=findings,
    )
    study = SimpleNamespace(st_part="1", st_modal="2", st_disease="두개내출혈", st_image="/brain/a.png")
    monkeypatch.setattr(learning.settings, "nas_public_base_url", "https://nas.example/public")
    monkeypatch.setattr(
        review_svc.repositories,
        "get_owned",
        lambda db, review_idx, user_idx: (review, study),
    )
    body = review_svc.get_saved_review(SimpleNamespace(), 100, 3)
    assert body["user_roi"]["x"] == 0.2
    assert body["user_rois"][0]["width"] == 0.25
    assert body["abnormality_bbox"]["y"] == 0.31


def test_grade_findings_json_keeps_user_roi():
    raw = review_svc.grade_findings_json(
        {
            "classification": {
                "top_label": "any",
                "top_label_ko": "두개내출혈",
                "confidence": 0.8,
                "findings": [{"label": "any", "label_ko": "두개내출혈", "score": 0.8}],
            },
            "user_roi_bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            "roi_type": "box",
        }
    )
    data = json.loads(raw)
    assert data["user_roi"]["type"] == "box"
    assert data["user_roi"]["width"] == 0.3
    assert data["classification"]["top_label_ko"] == "두개내출혈"
