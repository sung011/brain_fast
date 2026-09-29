"""
무릎 MRI 분석(class/knee) 테스트.

가중치를 내려받지 않고 가짜 모델로 확인한다. 실제 가중치 확인은 KNEE_RUN_REAL_WEIGHTS=1 일 때만.
실행: python -m pytest tests/test_knee.py  (torch 가 설치된 환경)
"""

from __future__ import annotations

import importlib
import os
from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")

knee_config = importlib.import_module("class.knee.config")
knee_errors = importlib.import_module("class.knee.errors")
knee_pre = importlib.import_module("class.knee.preprocess")
knee_ens = importlib.import_module("class.knee.ensemble")
knee_loc = importlib.import_module("class.knee.localization")
knee_model = importlib.import_module("class.knee.model")

KneeInputError = knee_errors.KneeInputError
KneeAnalysisError = knee_errors.KneeAnalysisError
Preprocess = knee_pre.Preprocess


def _vol(s=4, h=32, w=32, dtype="float32"):
    rng = np.random.default_rng(0)
    return (rng.random((s, h, w)) * 100).astype(dtype)


# ── 1. 전처리 ─────────────────────────────────────────────
@pytest.mark.parametrize("crop", [None, 0.7])
def test_preprocess_shape_and_finite(crop):
    x = Preprocess.preprocess_volume(_vol(), crop=crop)
    assert tuple(x.shape) == (4, 3, 224, 224)
    assert x.dtype == torch.float32
    assert torch.isfinite(x).all()


def test_preprocess_constant_volume_is_finite():
    x = Preprocess.preprocess_volume(np.full((3, 16, 16), 7, dtype="float32"))
    assert torch.isfinite(x).all()


# ── 2. 입력 검증(추론 전에 거부) ───────────────────────────
@pytest.mark.parametrize(
    "bad",
    [
        np.zeros((0, 16, 16), dtype="float32"),
        np.zeros((4, 0, 16), dtype="float32"),
        np.zeros((16, 16), dtype="float32"),
        np.zeros((2, 4, 16, 16), dtype="float32"),
        np.array([[["a"]]]),
        np.zeros((2, 8, 8), dtype="complex64"),
        np.zeros((2, 8, 8), dtype=bool),
        np.full((2, 8, 8), np.nan, dtype="float32"),
        np.full((2, 8, 8), np.inf, dtype="float32"),
        np.zeros((129, 8, 8), dtype="float32"),
        np.zeros((2, 1025, 8), dtype="float32"),
    ],
)
def test_bad_volume_rejected_before_inference(bad):
    with pytest.raises(KneeInputError):
        Preprocess.validate_volume(bad)


def test_not_an_array_rejected():
    with pytest.raises(KneeInputError):
        Preprocess.validate_volume([[[1.0]]])


def test_total_voxel_limit_rejected(monkeypatch):
    monkeypatch.setattr(knee_pre, "MAX_VOXELS", 100)
    with pytest.raises(KneeInputError):
        Preprocess.validate_volume(np.zeros((2, 8, 8), dtype="float32"))


# ── 3. 게이팅 경계 ────────────────────────────────────────
@pytest.mark.parametrize(
    "p, confident, verdict",
    [
        (0.399, True, "이상 소견 가능성 낮음"),
        (0.40, False, "판단 어려움"),
        (0.50, False, "판단 어려움"),
        (0.60, False, "판단 어려움"),
        (0.601, True, "이상 소견 가능성 높음"),
    ],
)
def test_gating_boundaries(p, confident, verdict):
    got_confident, got_verdict = knee_ens.KneeEnsembleService.judge("abnormal", p)
    assert got_confident is confident
    assert got_verdict == verdict


def test_gating_uses_unrounded_probability():
    # 표시용 확률은 소수 셋째 자리로 반올림하지만 판정은 반올림 전 값으로 한다.
    confident, _ = knee_ens.KneeEnsembleService.judge("acl", 0.39996)
    assert confident is True


# ── 4. 설정: 환경 변수 > .env > 없음 ───────────────────────
def test_setting_env_beats_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("KNEE_HF_REPO=from/dotenv\n", encoding="utf-8")
    monkeypatch.setattr(knee_config, "ENV_PATH", env_file)
    monkeypatch.setenv("KNEE_HF_REPO", "from/environ")
    assert knee_config.hf_repo() == "from/environ"


def test_setting_dotenv_fallback_and_import_order(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('# 주석\nKNEE_HF_REPO="from/dotenv"\n', encoding="utf-8")
    monkeypatch.setattr(knee_config, "ENV_PATH", env_file)
    monkeypatch.delenv("KNEE_HF_REPO", raising=False)
    assert knee_config.hf_repo() == "from/dotenv"
    monkeypatch.setenv("KNEE_HF_REPO", "later/environ")
    assert knee_config.hf_repo() == "later/environ"


def test_setting_missing_everywhere(tmp_path, monkeypatch):
    monkeypatch.setattr(knee_config, "ENV_PATH", tmp_path / "없음.env")
    monkeypatch.delenv("KNEE_HF_REPO", raising=False)
    assert knee_config.hf_repo() == ""
    svc = knee_ens.KneeEnsembleService()
    with pytest.raises(RuntimeError):
        svc.load()
    assert svc.ready is False and svc.error


# ── 5. 모델 로드 실패/복구 (네트워크는 가짜) ───────────────
def _tiny_ckpt(path):
    m = knee_model.MRNetModel()
    torch.save({"model": m.state_dict(), "backbone": "resnet18", "pool": "max", "crop": None}, path)


def test_load_failure_then_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(knee_ens, "CKPT_FILES", {("acl", "sagittal"): ["a.pt"]})
    bad = tmp_path / "bad.pt"
    bad.write_bytes(b"not a checkpoint")
    good = tmp_path / "good.pt"
    _tiny_ckpt(good)
    combiner = tmp_path / "combiner.json"
    combiner.write_text('{"acl": {"planes": ["sagittal"]}}', encoding="utf-8")
    calib = tmp_path / "calibration.json"
    calib.write_text("{}", encoding="utf-8")

    state = {"ckpt": bad}

    def fake_download(self, filename):
        if filename.endswith(".pt"):
            return state["ckpt"]
        return combiner if filename == knee_config.COMBINER_FILE else calib

    monkeypatch.setattr(knee_ens.KneeEnsembleService, "_download", fake_download)
    svc = knee_ens.KneeEnsembleService()
    with pytest.raises(Exception):
        svc.load()
    assert svc.ready is False and svc.error

    state["ckpt"] = good
    svc.load()
    assert svc.ready is True and svc.error is None


def test_download_failure_keeps_not_ready(monkeypatch):
    def boom(self, filename):
        raise OSError("네트워크 실패")

    monkeypatch.setattr(knee_ens.KneeEnsembleService, "_download", boom)
    svc = knee_ens.KneeEnsembleService()
    with pytest.raises(OSError):
        svc.load()
    assert svc.ready is False
    with pytest.raises(RuntimeError):
        svc.predict({"axial": _vol()})


# ── 6. 촬영면 조합·결측 표시·NaN 방지 ──────────────────────
def _fake_service(monkeypatch, planes_probs):
    """모델 없이 predict 흐름만 확인하는 서비스. planes_probs: {(task,plane): 확률}."""
    svc = knee_ens.KneeEnsembleService()
    svc.ready = True
    svc.models = {key: [(object(), None)] for key in planes_probs}
    svc.combiner = {
        "abnormal": {"planes": ["axial", "coronal", "sagittal"]},
        "acl": {"planes": ["axial", "coronal", "sagittal"]},
        "meniscus": {"planes": ["axial", "coronal"]},
    }
    svc.calibration = {}
    monkeypatch.setattr(svc, "_plane_prob", lambda task, plane, vol: planes_probs[(task, plane)])
    return svc


ALL = {
    (t, p): 0.2
    for t in ("abnormal", "acl")
    for p in ("axial", "coronal", "sagittal")
}
ALL.update({("meniscus", "axial"): 0.2, ("meniscus", "coronal"): 0.2})


def test_all_planes_evaluated(monkeypatch):
    svc = _fake_service(monkeypatch, ALL)
    out = svc.predict({p: _vol() for p in ("axial", "coronal", "sagittal")})
    assert set(out) == {"abnormal", "acl", "meniscus"}
    assert all(v["status"] == "evaluated" for v in out.values())
    assert out["meniscus"]["planes_used"] == ["axial", "coronal"]
    assert out["meniscus"]["missing_planes"] == []


def test_sagittal_only_marks_meniscus_not_evaluated(monkeypatch):
    svc = _fake_service(monkeypatch, ALL)
    out = svc.predict({"sagittal": _vol()})
    assert out["acl"]["status"] == "evaluated"
    assert out["acl"]["missing_planes"] == ["axial", "coronal"]
    assert out["meniscus"]["status"] == "not_evaluated"
    assert "prob" not in out["meniscus"]
    assert out["meniscus"]["required_planes"] == ["axial", "coronal"]
    assert out["meniscus"]["reason"]


def test_unknown_plane_and_empty_input_rejected(monkeypatch):
    svc = _fake_service(monkeypatch, ALL)
    with pytest.raises(KneeInputError):
        svc.predict({"oblique": _vol()})
    with pytest.raises(KneeInputError):
        svc.predict({})


def test_nan_volume_never_becomes_low_risk(monkeypatch):
    svc = _fake_service(monkeypatch, ALL)
    bad = np.full((2, 8, 8), np.nan, dtype="float32")
    with pytest.raises(KneeInputError):
        svc.predict({"axial": bad})


@pytest.mark.parametrize("bad_prob", [float("nan"), float("inf"), -0.1, 1.5])
def test_model_returning_nonfinite_probability_fails(monkeypatch, bad_prob):
    probs = dict(ALL)
    probs[("acl", "axial")] = bad_prob
    svc = _fake_service(monkeypatch, probs)
    with pytest.raises(KneeAnalysisError):
        svc.predict({p: _vol() for p in ("axial", "coronal", "sagittal")})


def test_nan_calibration_fails(monkeypatch):
    svc = _fake_service(monkeypatch, ALL)
    svc.calibration = {"acl": {"platt_a": float("nan"), "platt_b": 0.0}}
    with pytest.raises(KneeAnalysisError):
        svc.predict({p: _vol() for p in ("axial", "coronal", "sagittal")})


# ── 7. ACL 박스 ───────────────────────────────────────────
def _fake_yolo(boxes):
    """boxes: [(x,y,w,h,conf)] 또는 빈 리스트."""
    class Boxes:
        def __init__(self):
            self.xywhn = torch.tensor([b[:4] for b in boxes]).reshape(-1, 4)
            self.conf = torch.tensor([b[4] for b in boxes])

        def __len__(self):
            return len(self.conf)

    return SimpleNamespace(predict=lambda _img, **_kw: [SimpleNamespace(boxes=Boxes())])


def test_locate_acl_box_and_null(monkeypatch):
    gray = np.random.default_rng(1).random((64, 64)).astype("float32")
    monkeypatch.setattr(knee_loc.Localization, "_get_yolo", classmethod(lambda cls: _fake_yolo([(0.4, 0.4, 0.2, 0.3, 0.9), (0.6, 0.6, 0.1, 0.1, 0.3)])))
    box = knee_loc.Localization.locate_acl(gray)
    assert box["label"] == "acl_region"
    assert box["confidence"] == pytest.approx(0.9)
    assert all(0.0 <= box[k] <= 1.0 for k in ("x_center", "y_center", "width", "height"))

    monkeypatch.setattr(knee_loc.Localization, "_get_yolo", classmethod(lambda cls: _fake_yolo([])))
    assert knee_loc.Localization.locate_acl(gray) is None


def test_locate_acl_rejects_bad_image():
    with pytest.raises(KneeInputError):
        knee_loc.Localization.locate_acl(np.full((8, 8), np.nan, dtype="float32"))
    with pytest.raises(KneeInputError):
        knee_loc.Localization.locate_acl(np.zeros((2, 8, 8), dtype="float32"))


# ── 8. Grad-CAM 오버레이 ───────────────────────────────────
def _gray():
    g = np.zeros((64, 64), dtype="float32")
    g[16:48, 16:48] = 0.8
    return g


def test_overlay_zero_cam_returns_original_and_no_activation():
    gray = _gray()
    img, activated = knee_loc.Localization.build_overlay(gray, np.zeros_like(gray))
    assert activated is False
    base = np.stack([np.clip(gray, 0, 1) * 255] * 3, axis=-1).astype("uint8")
    assert np.array_equal(np.asarray(img), base)


def test_overlay_constant_positive_cam_is_no_activation():
    gray = _gray()
    _, activated = knee_loc.Localization.build_overlay(gray, np.full_like(gray, 0.5))
    assert activated is False


def test_overlay_empty_body_is_no_activation():
    gray = np.zeros((64, 64), dtype="float32")
    cam = np.random.default_rng(2).random((64, 64)).astype("float32")
    _, activated = knee_loc.Localization.build_overlay(gray, cam)
    assert activated is False


def _changed_pixels(gray, img):
    base = np.stack([np.clip(gray, 0, 1) * 255] * 3, axis=-1).astype("uint8")
    return np.argwhere((np.asarray(img) != base).any(axis=-1))


def test_overlay_sparse_cam_does_not_highlight_far_from_response():
    # 반응이 한 점뿐이면 상위 15% 경계값이 0 이 된다. 그래도 반응 없는 영역은 칠하면 안 된다.
    gray = _gray()
    cam = np.zeros_like(gray)
    cam[32, 32] = 1.0
    img, activated = knee_loc.Localization.build_overlay(gray, cam)
    assert activated is True
    changed = _changed_pixels(gray, img)
    assert len(changed) > 0
    far = np.hypot(changed[:, 0] - 32, changed[:, 1] - 32)
    assert far.max() <= 8, f"반응점에서 {far.max():.1f}px 떨어진 곳까지 강조됨"
    assert len(changed) < 400  # 몸 영역 전체(32x32=1024)가 아니어야 한다
    corner = np.asarray(img)[19:23, 19:23]
    base = np.stack([np.clip(gray, 0, 1) * 255] * 3, axis=-1).astype("uint8")
    assert np.array_equal(corner, base[19:23, 19:23])


def test_overlay_response_only_outside_body_is_no_activation():
    gray = _gray()
    cam = np.zeros_like(gray)
    cam[2, 2] = 1.0  # 검은 배경 모서리에서만 반응
    img, activated = knee_loc.Localization.build_overlay(gray, cam)
    assert activated is False
    assert len(_changed_pixels(gray, img)) == 0


def test_overlay_valid_cam_highlights_and_shape_mismatch_rejected():
    gray = _gray()
    cam = np.zeros_like(gray)
    cam[24:32, 24:32] = 1.0
    img, activated = knee_loc.Localization.build_overlay(gray, cam)
    assert activated is True
    base = np.stack([np.clip(gray, 0, 1) * 255] * 3, axis=-1).astype("uint8")
    assert not np.array_equal(np.asarray(img), base)
    with pytest.raises(KneeInputError):
        knee_loc.Localization.build_overlay(gray, cam[:32])
    with pytest.raises(KneeInputError):
        knee_loc.Localization.build_overlay(gray, np.full_like(gray, np.nan))


# ── 9. Grad-CAM hook 정리 ─────────────────────────────────
class _Feat(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.layer3 = torch.nn.Conv2d(3, 4, 3, padding=1)

    def forward(self, x):
        return self.layer3(x).mean(dim=(2, 3))


class _FailingModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.features = _Feat()

    def forward(self, x):
        self.features(x)
        raise RuntimeError("forward 실패")


class _OkModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.features = _Feat()
        self.head = torch.nn.Linear(4, 1)

    def forward(self, x):
        f = self.features(x).max(dim=0, keepdim=True).values
        return self.head(f)


def test_failed_gradcam_leaves_no_hook_and_next_predict_works():
    model = _FailingModel()
    with pytest.raises(Exception):
        knee_loc.Localization.grad_cam(model, _vol(), None, "cpu")
    assert len(model.features.layer3._forward_hooks) == 0

    ok = _OkModel()
    cam, prob, top = knee_loc.Localization.grad_cam(ok, _vol(), None, "cpu")
    assert len(ok.features.layer3._forward_hooks) == 0
    assert 0.0 <= prob <= 1.0 and np.isfinite(cam).all()
    with torch.no_grad():
        ok(Preprocess.preprocess_volume(_vol()))


# ── 10. 실제 가중치 스모크 (선택) ──────────────────────────
@pytest.mark.skipif(os.environ.get("KNEE_RUN_REAL_WEIGHTS") != "1", reason="KNEE_RUN_REAL_WEIGHTS=1 일 때만")
def test_real_weights_smoke():
    svc = knee_ens.KneeEnsembleService()
    svc.load()
    assert sum(len(v) for v in svc.models.values()) == 14
    out = svc.predict({p: _vol(8, 64, 64) for p in ("axial", "coronal", "sagittal")})
    assert set(out) == {"abnormal", "acl", "meniscus"}
    assert all(np.isfinite(v["prob"]) for v in out.values())
