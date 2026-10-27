"""
무릎 MRI 분석(class/knee) 테스트.

가중치를 내려받지 않고 가짜 모델로 확인한다. 실제 가중치 확인은 KNEE_RUN_REAL_WEIGHTS=1 일 때만.
실행: python -m pytest tests/test_knee.py  (torch 가 설치된 환경)
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
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


# ── 4-1. 무릎 전용 토큰(KNEE_HF_TOKEN): 비공개 저장소용. 비어 있으면 지금과 같은 동작 ──
# 진짜 토큰이 아니다. 시험에서만 쓰는 가짜 값이다.
FAKE_TOKEN = "hf_FAKE_TOKEN_FOR_TESTS_ONLY"


def test_hf_token_env_beats_dotenv_and_missing_is_none(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("KNEE_HF_TOKEN=from_dotenv\n", encoding="utf-8")
    monkeypatch.setattr(knee_config, "ENV_PATH", env_file)
    monkeypatch.delenv("KNEE_HF_TOKEN", raising=False)
    assert knee_config.hf_token() == "from_dotenv"
    monkeypatch.setenv("KNEE_HF_TOKEN", "from_environ")
    assert knee_config.hf_token() == "from_environ"
    monkeypatch.delenv("KNEE_HF_TOKEN")
    monkeypatch.setattr(knee_config, "ENV_PATH", tmp_path / "없음.env")
    assert knee_config.hf_token() is None


def test_hf_token_never_falls_back_to_the_brain_token(tmp_path, monkeypatch):
    """뇌용 HF_TOKEN(다른 계정)을 무릎 토큰으로 몰래 쓰지 않는다. 무릎 전용 칸이 비면 None 이다."""
    monkeypatch.setattr(knee_config, "ENV_PATH", tmp_path / "없음.env")
    monkeypatch.delenv("KNEE_HF_TOKEN", raising=False)
    monkeypatch.setenv("HF_TOKEN", "brain_token_must_not_be_used")
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "brain_token_must_not_be_used")
    assert knee_config.hf_token() is None


def _capture_download(monkeypatch, module, tmp_path):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        path = tmp_path / "x.json"
        path.write_text("{}", encoding="utf-8")
        return str(path)

    monkeypatch.setattr(module, "hf_hub_download", fake)
    return calls


def test_ensemble_download_passes_the_knee_token(tmp_path, monkeypatch):
    monkeypatch.setenv("KNEE_HF_REPO", "me/Knee")
    monkeypatch.setenv("KNEE_HF_REVISION", "abc123")
    monkeypatch.setenv("KNEE_HF_TOKEN", FAKE_TOKEN)
    calls = _capture_download(monkeypatch, knee_ens, tmp_path)
    knee_ens.KneeEnsembleService._download("combiner.json")
    assert calls == [{"repo_id": "me/Knee", "filename": "combiner.json", "revision": "abc123", "token": FAKE_TOKEN}]


def test_ensemble_download_without_knee_token_sends_token_false(tmp_path, monkeypatch):
    """의도적인 계약 변경(12차 TOKEN-012-01): 무릎 토큰이 없으면 None 이 아니라 False 를 넘긴다.
    None 은 'huggingface_hub 가 환경 변수·로그인 캐시에서 알아서 찾아 쓰라'는 뜻이라 뇌 토큰이 몰래 쓰인다."""
    monkeypatch.setattr(knee_config, "ENV_PATH", tmp_path / "없음.env")
    monkeypatch.setenv("KNEE_HF_REPO", "me/Knee")
    monkeypatch.delenv("KNEE_HF_REVISION", raising=False)
    monkeypatch.delenv("KNEE_HF_TOKEN", raising=False)
    calls = _capture_download(monkeypatch, knee_ens, tmp_path)
    knee_ens.KneeEnsembleService._download("combiner.json")
    assert calls[0]["token"] is False


def test_yolo_download_passes_the_knee_token(tmp_path, monkeypatch):
    import sys

    monkeypatch.setenv("KNEE_HF_REPO", "me/Knee")
    monkeypatch.setenv("KNEE_HF_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(knee_loc.Localization, "_yolo", None)
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda path: ("fake-yolo", path)))
    calls = _capture_download(monkeypatch, knee_loc, tmp_path)
    knee_loc.Localization._get_yolo()
    assert calls[0]["token"] == FAKE_TOKEN
    assert calls[0]["filename"] == knee_config.ACL_YOLO_FILE


def test_token_is_not_leaked_in_error_text(monkeypatch):
    """최악의 경우(예외 문구에 토큰이 섞임)에도 API 안내문과 내부 오류 기록에 토큰이 남지 않는다."""
    monkeypatch.setenv("KNEE_HF_REPO", "me/Knee")
    monkeypatch.setenv("KNEE_HF_TOKEN", FAKE_TOKEN)

    def boom(**kwargs):
        raise OSError(f"401 Unauthorized for token {kwargs['token']}")

    monkeypatch.setattr(knee_ens, "hf_hub_download", boom)
    svc = knee_ens.KneeEnsembleService()
    with pytest.raises(knee_errors.KneeModelNotReady) as exc:
        svc.ensure_ready()
    assert FAKE_TOKEN not in str(exc.value)
    assert svc.ready is False and svc.error and FAKE_TOKEN not in svc.error


# ── 4-2. 12차 TOKEN-012-01: 실제 헤더 선택 경로에서 뇌·로그인 캐시 토큰이 쓰이지 않는다 ──
def _run_download_path(path_name, monkeypatch, tmp_path):
    """무릎 다운로드 두 경로(앙상블·YOLO) 중 하나를 실행하고, hf_hub_download 에 넘어간 인자 목록을 돌려준다."""
    import sys

    module = knee_ens if path_name == "ensemble" else knee_loc
    calls = _capture_download(monkeypatch, module, tmp_path)
    if path_name == "ensemble":
        knee_ens.KneeEnsembleService._download("combiner.json")
    else:
        monkeypatch.setattr(knee_loc.Localization, "_yolo", None)
        monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda path: ("fake-yolo", path)))
        knee_loc.Localization._get_yolo()
    return calls


def _headers_for(token_arg):
    """huggingface_hub 가 실제 요청에 쓰는 함수(build_hf_headers)로 Authorization 헤더가 어떻게 정해지는지 본다(네트워크 없음)."""
    from huggingface_hub.utils import build_hf_headers

    return {k.lower(): v for k, v in build_hf_headers(token=token_arg).items()}


@pytest.fixture
def brain_and_login_tokens(tmp_path, monkeypatch):
    """무릎 토큰은 없고, 프로세스 HF_TOKEN(뇌 설정이 올리는 값)과 로그인 캐시 토큰만 있는 상황을 만든다."""
    import huggingface_hub

    cached = tmp_path / "login_token"
    cached.write_text("hf_FAKE_CACHED_LOGIN_TOKEN", encoding="utf-8")
    monkeypatch.setattr(huggingface_hub.constants, "HF_TOKEN_PATH", str(cached))
    monkeypatch.setenv("HF_TOKEN", "hf_FAKE_BRAIN_TOKEN")
    monkeypatch.delenv("HF_HUB_DISABLE_IMPLICIT_TOKEN", raising=False)
    monkeypatch.setattr(knee_config, "ENV_PATH", tmp_path / "없음.env")
    monkeypatch.setenv("KNEE_HF_REPO", "me/Knee")
    monkeypatch.delenv("KNEE_HF_TOKEN", raising=False)
    # 기준선: 이 환경에서 token=None 이면 huggingface_hub 가 실제로 뇌 토큰을 Authorization 에 싣는다(이게 12차가 짚은 위험)
    assert _headers_for(None).get("authorization") == "Bearer hf_FAKE_BRAIN_TOKEN"


@pytest.mark.parametrize("path_name", ["ensemble", "yolo"])
def test_no_knee_token_sends_no_authorization_header(path_name, tmp_path, monkeypatch, brain_and_login_tokens):
    calls = _run_download_path(path_name, monkeypatch, tmp_path)
    assert calls[0]["token"] is False
    headers = _headers_for(calls[0]["token"])
    assert "authorization" not in headers   # 뇌 HF_TOKEN·로그인 캐시 토큰 모두 쓰이지 않는다


@pytest.mark.parametrize("path_name", ["ensemble", "yolo"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_knee_token_is_treated_as_missing(path_name, blank, tmp_path, monkeypatch, brain_and_login_tokens):
    monkeypatch.setenv("KNEE_HF_TOKEN", blank)
    calls = _run_download_path(path_name, monkeypatch, tmp_path)
    assert calls[0]["token"] is False
    assert "authorization" not in _headers_for(calls[0]["token"])


@pytest.mark.parametrize("path_name", ["ensemble", "yolo"])
def test_knee_token_alone_is_sent_even_when_brain_token_exists(path_name, tmp_path, monkeypatch, brain_and_login_tokens):
    monkeypatch.setenv("KNEE_HF_TOKEN", FAKE_TOKEN)
    calls = _run_download_path(path_name, monkeypatch, tmp_path)
    assert calls[0]["token"] == FAKE_TOKEN
    assert _headers_for(calls[0]["token"])["authorization"] == f"Bearer {FAKE_TOKEN}"   # 뇌·캐시 토큰이 아니라 무릎 토큰만


def test_env_beats_dotenv_at_the_download_boundary(tmp_path, monkeypatch, brain_and_login_tokens):
    env_file = tmp_path / ".env"
    env_file.write_text("KNEE_HF_TOKEN=hf_FROM_DOTENV_FILE\n", encoding="utf-8")
    monkeypatch.setattr(knee_config, "ENV_PATH", env_file)
    calls = _run_download_path("ensemble", monkeypatch, tmp_path)
    assert calls[0]["token"] == "hf_FROM_DOTENV_FILE"          # .env 만 있으면 .env 값
    monkeypatch.setenv("KNEE_HF_TOKEN", "hf_FROM_ENVIRONMENT")
    calls = _run_download_path("ensemble", monkeypatch, tmp_path)
    assert calls[-1]["token"] == "hf_FROM_ENVIRONMENT"         # 환경 변수가 우선


# ── 4-3. 12차 TOKEN-012-02: 직접 호출(load·YOLO)의 예외·traceback·로그에도 토큰이 남지 않는다 ──
def _boom_with_token(**kwargs):
    """최악의 경우: 예외 문구와 원인(cause) 예외에 모두 토큰이 섞여 있다."""
    token = kwargs["token"]
    try:
        raise ValueError(f"inner cause carrying {token}")
    except ValueError as inner:
        raise OSError(f"401 Unauthorized for token {token}") from inner


def _all_text(exc):
    import traceback

    return str(exc) + repr(exc) + "".join(traceback.format_exception(exc))


def _assert_clean(exc, *secrets):
    text = _all_text(exc)
    for secret in secrets:
        assert secret not in text
    assert exc.__cause__ is None and exc.__context__ is None   # 원 예외를 cause/context 로 다시 노출하지 않는다


def test_load_direct_call_error_has_no_token(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("KNEE_HF_REPO", "me/Knee")
    monkeypatch.setenv("KNEE_HF_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(knee_ens, "hf_hub_download", _boom_with_token)
    svc = knee_ens.KneeEnsembleService()
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(knee_errors.KneeModelLoadError) as exc:
            svc.load()
    _assert_clean(exc.value, FAKE_TOKEN)
    assert "OSError" in str(exc.value)                         # 디버깅에 필요한 종류는 남긴다
    assert svc.ready is False and svc.error and FAKE_TOKEN not in svc.error
    assert FAKE_TOKEN not in caplog.text


def test_yolo_direct_call_error_has_no_token_and_can_retry(monkeypatch, caplog):
    import logging
    import sys

    monkeypatch.setenv("KNEE_HF_REPO", "me/Knee")
    monkeypatch.setenv("KNEE_HF_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(knee_loc.Localization, "_yolo", None)
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda path: ("fake-yolo", path)))
    monkeypatch.setattr(knee_loc, "hf_hub_download", _boom_with_token)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(knee_errors.KneeModelLoadError) as exc:
            knee_loc.Localization._get_yolo()
    _assert_clean(exc.value, FAKE_TOKEN)
    assert knee_loc.Localization._yolo is None                 # 실패 뒤 다시 시도할 수 있다
    assert FAKE_TOKEN not in caplog.text


def test_load_sanitizes_errors_that_did_not_pass_through_the_download_wrapper(monkeypatch, caplog):
    """_download 자체가 (가짜로) 토큰이 섞인 예외를 던져도 load() 가 한 번 더 정리한다(체크포인트 읽기 오류 등 다른 경로 포함)."""
    import logging

    monkeypatch.setenv("KNEE_HF_REPO", "me/Knee")
    monkeypatch.setenv("KNEE_HF_TOKEN", FAKE_TOKEN)

    def raw_boom(filename):
        try:
            raise ValueError(f"inner {FAKE_TOKEN}")
        except ValueError as inner:
            raise OSError(f"failed with {FAKE_TOKEN} and another hf_AnotherTokenShape1234567890") from inner

    monkeypatch.setattr(knee_ens.KneeEnsembleService, "_download", staticmethod(raw_boom))
    svc = knee_ens.KneeEnsembleService()
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(knee_errors.KneeModelLoadError) as exc:
            svc.load()
    _assert_clean(exc.value, FAKE_TOKEN, "hf_AnotherTokenShape1234567890")
    assert svc.error and FAKE_TOKEN not in svc.error and "hf_AnotherTokenShape1234567890" not in svc.error
    assert FAKE_TOKEN not in caplog.text


def test_ensure_ready_error_has_no_token_in_message_or_traceback(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("KNEE_HF_REPO", "me/Knee")
    monkeypatch.setenv("KNEE_HF_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(knee_ens, "hf_hub_download", _boom_with_token)
    svc = knee_ens.KneeEnsembleService()
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(knee_errors.KneeModelNotReady) as exc:
            svc.ensure_ready()
    assert FAKE_TOKEN not in _all_text(exc.value)
    assert "준비하지 못했습니다" in str(exc.value)             # API 고정 안내는 그대로
    assert FAKE_TOKEN not in caplog.text


def test_redact_secrets_hides_the_knee_token_and_any_hf_token_shape(monkeypatch):
    monkeypatch.setenv("KNEE_HF_TOKEN", "custom-token-without-prefix")
    text = "a custom-token-without-prefix b hf_BrainTokenLooksLikeThis12345 c"
    out = knee_config.redact_secrets(text)
    assert "custom-token-without-prefix" not in out and "hf_BrainTokenLooksLikeThis12345" not in out
    assert out.startswith("a ") and out.endswith(" c")          # 나머지 문장은 그대로


def test_failure_then_recovery_still_works_after_sanitizing(tmp_path, monkeypatch):
    """기존 복구 동작 유지: 다운로드 실패 → ready=False, 이후 정상으로 돌아오면 다시 적재."""
    monkeypatch.setenv("KNEE_HF_REPO", "me/Knee")
    monkeypatch.setenv("KNEE_HF_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(knee_ens, "CKPT_FILES", {("acl", "sagittal"): ["a.pt"]})
    good = tmp_path / "good.pt"
    _tiny_ckpt(good)
    combiner = tmp_path / "combiner.json"
    combiner.write_text('{"acl": {"planes": ["sagittal"]}}', encoding="utf-8")
    calib = tmp_path / "calibration.json"
    calib.write_text("{}", encoding="utf-8")
    state = {"fail": True}

    def flaky(**kwargs):
        if state["fail"]:
            return _boom_with_token(**kwargs)
        name = kwargs["filename"]
        return str(good if name.endswith(".pt") else (combiner if name == knee_config.COMBINER_FILE else calib))

    monkeypatch.setattr(knee_ens, "hf_hub_download", flaky)
    svc = knee_ens.KneeEnsembleService()
    with pytest.raises(knee_errors.KneeModelLoadError):
        svc.load()
    assert svc.ready is False and svc.error
    state["fail"] = False
    svc.load()
    assert svc.ready is True and svc.error is None


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
    # 12차 TOKEN-012-02: load() 는 원 예외(OSError) 대신 정리된 KneeModelLoadError(RuntimeError)를 내보낸다. 원 예외 종류는 문구에 남는다.
    with pytest.raises(knee_errors.KneeModelLoadError, match="OSError"):
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


# ── 11. 화면 캡처 한 장 분석용 메서드 (API 가 승인된 조합에서만 부른다) ──────
def _fake_ready_service():
    svc = knee_ens.KneeEnsembleService()
    svc.device = "cpu"
    svc.models = {("abnormal", "sagittal"): [(knee_model.MRNetModel().eval(), None)]}
    svc.ready = True
    return svc


def _random_gray(seed=0, size=96):
    return np.random.default_rng(seed).integers(0, 255, (size, size), dtype=np.uint8)


def test_single_plane_prob_scores_one_image():
    p = _fake_ready_service().single_plane_prob("abnormal", "sagittal", _random_gray())
    assert 0.0 <= p <= 1.0


def test_single_plane_methods_reject_missing_model_and_bad_shape():
    svc = _fake_ready_service()
    with pytest.raises(KneeInputError):
        svc.single_plane_prob("meniscus", "sagittal", _random_gray())      # 서비스에 없는 조합
    with pytest.raises(KneeInputError):
        svc.single_plane_prob("abnormal", "sagittal", _random_gray()[None])  # 2차원이 아님
    with pytest.raises(KneeInputError):
        svc.single_plane_gradcam("acl", "axial", _random_gray())


def test_single_plane_gradcam_returns_model_input_sized_overlay():
    image, activated = _fake_ready_service().single_plane_gradcam("abnormal", "sagittal", _random_gray())
    assert image.size == (224, 224)
    assert isinstance(activated, bool)


def test_ensure_ready_hides_the_internal_reason(monkeypatch):
    svc = knee_ens.KneeEnsembleService()

    def boom(filename):
        raise OSError("SECRET-REPO/private path token")

    monkeypatch.setattr(knee_ens.KneeEnsembleService, "_download", staticmethod(boom))
    with pytest.raises(knee_errors.KneeModelNotReady) as info:
        svc.ensure_ready()
    assert "SECRET" not in str(info.value)
    assert info.value.__cause__ is None
    assert svc.ready is False


def test_package_import_is_lazy_for_model_code():
    import subprocess
    import sys

    code = ("import importlib, sys; importlib.import_module('class.knee.gates'); importlib.import_module('class.knee.screen_roi'); "
            "importlib.import_module('class.knee.imageio'); print('torch' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip() == "False", "게이트·이미지·판정 모듈이 torch 를 불러옵니다"
