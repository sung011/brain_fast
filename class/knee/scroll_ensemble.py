"""
'넘기며 분석' 앙상블(기존 B + v4) 점수 — MSN-20261102-004 (설계 ENSEMBLE_SCREEN_PLAN · ENSEMBLE_RUNTIME_CONTRACT r1).

한 줄 요약: 서버가 고른 같은 15장을 기존 B(볼륨 모델)와 v4(장별 YOLO 크롭 시리즈 모델)에 넣어 두 logit 을 고정 식으로 합친다.

- 켜기: 실행 프로세스 환경 변수 KNEE_SCROLL_ENSEMBLE_ENABLED=1 일 때만 ensemble 요청을 받는다(.env 는 보지 않는다). 기본은 기존 B.
- 고정값: 원 실험 spec(knee-legacy-v4-ensemble-feasibility-20261029-r1)의 식·α·τ 를 그대로 쓴다. 시연 결과를 보고 바꾸지 않는다.
  화면 입력은 원 실험 조건(원본 PNG·원 장 번호)과 다르므로 실행 프로필 knee-scroll-ensemble-local-20261102-r1 로 구분하고
  input_condition_verified=false 를 유지한다.
- 장 선택: 서버가 중복을 뺀 고유 15~20장 U 에서 가운데 15장(c=n//2, s=max(0,min(n-15,c-7))). B·YOLO/v4 모두 이 15장만 같은 순서로 쓴다.
  10~14장은 앙상블 미분석(장을 반복·보간해 채우지 않음).
- v4 입력: 장마다 YOLO 1회(CPU, imgsz 320, conf .25) → 최고 확신 상자 1개(동률은 앞 순번) → 원 float 좌표를 반올림·자르기 전에 검사 →
  정사각 1.5배 크롭(정수 기하는 원 실험 crop_geometry 와 같음) → uint8/255 → 224 bilinear(antialias) → ImageNet 정규화.
  유효 크롭이 max(3, ceil(.2·15))=3장 미만이면 앙상블 미분석. 다른 장 상자 복사·전체 장 대체·임계치 완화 없음.
- 미분석 전파: 한쪽 logit 이 없거나 크롭이 부족하면 점수·참고선 위치 모두 null. B 점수나 0·정상으로 대신하지 않는다.
- 모델 파일: 로컬 묶음(KNEE_SCROLL_LOCAL_BUNDLE_DIR)의 고정 manifest 만 읽고, 세 파일의 크기·SHA-256 을 이 파일의 상수와 대조한다.
  manifest 가 상수를 덮어쓸 수 없다. 요청으로 경로·모델 이름을 받지 않는다. 시연 중 내려받기 없음.
- 하지 않는 일: Grad-CAM(앙상블 설명이 아님), 확률·진단 판정, 가중치·τ 탐색, 재학습, 원 실험 파일 수정.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import threading
from pathlib import Path

import numpy as np

SPEC_ID = "knee-legacy-v4-ensemble-feasibility-20261029-r1"
RUNTIME_PROFILE_ID = "knee-scroll-ensemble-local-20261102-r1"
BUNDLE_KIND = "knee-scroll-ensemble-local-bundle-v1"
BUNDLE_MANIFEST = "knee_scroll_ensemble_bundle.json"

ALPHA = 0.5
TAU_L = 0.89
TAU_V = 0.08
TAU_E = 0.4798988972937373

K = 15
MIN_UNIQUE, MAX_UNIQUE = 15, 20
MIDDLE_INDEX = 7                       # 선택 15장 중 가운데(0부터) — 위치 그림용

# 고정 모델 지문(원 실험과 같은 파일만)
MODEL_FILES = {
    "legacy": {"sha256": "bb81d22bf4ee99ab1b74a004ace4167dddf5e4ac87b160f5f23dec9570477393", "bytes": 44788542},
    "v4": {"sha256": "80d75a0f8215627f5b3cc7c863cacf3e65d1e21a9eaf24d57942765c83d8efd1", "bytes": 44790411},
    "yolo": {"sha256": "d9699c11f153a1c7e13bd7a2a82b9661a8c7829e7cc0009ad5f2e12f53848508", "bytes": 6228900},
}
V4_MODEL_ZIP_SHA256 = "75474c48f5814f1e4550c5d8f0be5b0fb4462c874a5e262969d525e47a220c91"
V4_ZIP_MEMBER = "knee_acl_consistency_v4/best.pt"
# 묶음 manifest 의 출처(sources): v4 원 ZIP 안 부속 파일 지문(원 ZIP 에서 확인한 값과 정확히 같아야 함)
SOURCES_FIXED = {
    "v4_model_zip_sha256": V4_MODEL_ZIP_SHA256,
    "v4_zip_member": V4_ZIP_MEMBER,
    "v4_model_manifest_sha256": "1b054bccc0035cf9881be9b275258ddc258cdb70f8746a1e9513916565c51991",
    "v4_selection_sha256": "c373e43f767497ed79942dff771c78d922d7e9748f1ed0da6cc05aa8633e7f0f",
    "v4_run_protocol_sha256": "ab3f27f778743a24a59fadd2e2a00634afdf3502de67c815494cba8c18940641",
}
# 묶음을 만든 도구·환경 기록(built): 형식만 검사하는 참고 메타. 실행 환경과 같다고 간주하지 않는다.
BUILT_KEYS = ("tool", "tool_sha256", "python", "torch", "ultralytics")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# 원 실험(spec_v4 detector·crop·input) 값
DET_IMGSZ = 320
DET_CONF_MIN, DET_CONF_MAX = 0.25, 1.0
DET_EDGE_TOL = 1e-4
DET_CLASS_NAMES = {0: "acl_region"}
CROP_SCALE = 1.5
CROP_MIN_SIDE = 16
CROP_VALID_FRACTION_MIN = 0.5
VALID_CROP_MIN = 3
VALID_CROP_FRACTION_MIN = 0.2
G = 224
KMAX = 20
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

ENABLED_SETTING = "KNEE_SCROLL_ENSEMBLE_ENABLED"
BUNDLE_SETTING = "KNEE_SCROLL_LOCAL_BUNDLE_DIR"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

FRAME_STATUSES = ("found", "not_found", "invalid_box", "crop_rejected")


class EnsembleUnavailable(Exception):
    """모델 부재·지문 불일치·로드·검출 내부 오류(→ 503). reason 은 일반 코드만(경로·예외 원문 없음)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class BundleError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ── 설정(실행 프로세스 환경 변수만) ─────────────────────────────────────
def enabled() -> bool:
    return (os.environ.get(ENABLED_SETTING) or "").strip() == "1"


def bundle_dir() -> Path | None:
    value = (os.environ.get(BUNDLE_SETTING) or "").strip()
    return Path(value) if value else None


def _no_dupes(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("dup")
    return dict(pairs)


def _reject_constant(c):
    raise ValueError(c)


def _finite_float(text: str) -> float:
    """1e999·-1e999 처럼 실수 변환 결과가 inf 가 되는 값도 거절(parse_constant 는 NaN/Infinity 글자만 막는다)."""
    v = float(text)
    if not math.isfinite(v):
        raise ValueError("non-finite")
    return v


def _all_finite_json(v) -> bool:
    """파싱 결과 전체(중첩 포함)에 비유한 실수가 없는지 다시 확인."""
    if isinstance(v, float):
        return math.isfinite(v)
    if isinstance(v, dict):
        return all(isinstance(k, str) and _all_finite_json(x) for k, x in v.items())
    if isinstance(v, list):
        return all(_all_finite_json(x) for x in v)
    return v is None or isinstance(v, (str, int, bool))


def read_bundle_manifest(root: Path) -> dict:
    """로컬 묶음 manifest 를 엄격하게 읽는다.

    모든 멤버(최상위·중첩·부가 필드)의 NaN/Infinity/-Infinity/±1e999 거절, 중복 키 거절, 필드 집합·형식 고정,
    모델 지문·α·τ·출처 지문은 코드 상수와 정확히 같아야 한다. built 는 형식만 검사하는 참고 기록이다.
    """
    try:
        text = (root / BUNDLE_MANIFEST).read_text(encoding="utf-8")
    except OSError:
        raise BundleError("bundle_missing") from None
    try:
        m = json.loads(text, object_pairs_hook=_no_dupes, parse_constant=_reject_constant, parse_float=_finite_float)
    except (ValueError, TypeError, OverflowError):
        raise BundleError("bundle_manifest_invalid") from None
    if not _all_finite_json(m):
        raise BundleError("bundle_manifest_invalid")
    top = {"kind", "runtime_profile_id", "spec_id", "alpha", "tau_legacy", "tau_v4", "tau_e", "files", "sources", "built"}
    if not isinstance(m, dict) or set(m) != top:
        raise BundleError("bundle_manifest_invalid")
    fixed = {"kind": BUNDLE_KIND, "runtime_profile_id": RUNTIME_PROFILE_ID, "spec_id": SPEC_ID}
    if any(m[k] != v for k, v in fixed.items()):
        raise BundleError("bundle_manifest_mismatch")
    for k, v in (("alpha", ALPHA), ("tau_legacy", TAU_L), ("tau_v4", TAU_V), ("tau_e", TAU_E)):
        if type(m[k]) is not float or m[k] != v:
            raise BundleError("bundle_manifest_mismatch")
    files = m["files"]
    if not isinstance(files, dict) or set(files) != set(MODEL_FILES):
        raise BundleError("bundle_manifest_invalid")
    for role, want in MODEL_FILES.items():
        f = files[role]
        if not isinstance(f, dict) or set(f) != {"name", "bytes", "sha256"}:
            raise BundleError("bundle_manifest_invalid")
        if not isinstance(f["name"], str) or not _SAFE_NAME.match(f["name"]) or f["name"] == BUNDLE_MANIFEST:
            raise BundleError("bundle_manifest_invalid")
        if type(f["bytes"]) is not int or f["bytes"] != want["bytes"] or f["sha256"] != want["sha256"]:
            raise BundleError("bundle_manifest_mismatch")
    if len({files[r]["name"] for r in files}) != len(files):
        raise BundleError("bundle_manifest_invalid")
    src = m["sources"]
    if not isinstance(src, dict) or set(src) != set(SOURCES_FIXED) or not all(isinstance(v, str) for v in src.values()):
        raise BundleError("bundle_manifest_invalid")
    if any(src[k] != v for k, v in SOURCES_FIXED.items()):
        raise BundleError("bundle_manifest_mismatch")
    built = m["built"]
    if not isinstance(built, dict) or set(built) != set(BUILT_KEYS) or not all(isinstance(built[k], str) and 0 < len(built[k]) <= 200 for k in BUILT_KEYS):
        raise BundleError("bundle_manifest_invalid")
    if not _HEX64.match(built["tool_sha256"]):
        raise BundleError("bundle_manifest_invalid")
    return m


def check_bundle_config() -> None:
    """앙상블 요청마다(가벼운 점검): 로컬 묶음 설정과 manifest 가 유효한지. 아니면 EnsembleUnavailable(→503) — ENSEMBLE-LOCAL-001.

    이 점검이 통과하기 전에는 HF 다운로드·모델 로드·forward 를 하지 않는다.
    """
    root = bundle_dir()
    if root is None:
        raise EnsembleUnavailable("bundle_not_configured")
    try:
        read_bundle_manifest(root)
    except BundleError as exc:
        raise EnsembleUnavailable(exc.reason) from None


def check_bundle_files() -> None:
    """모델을 처음 읽기 전(무거운 점검): 세 파일 모두 크기·SHA 를 상수와 대조. 하나라도 없거나 다르면 EnsembleUnavailable."""
    check_bundle_config()
    try:
        for role in MODEL_FILES:
            read_bundle_bytes(role)
    except BundleError as exc:
        raise EnsembleUnavailable(exc.reason) from None


def read_bundle_bytes(role: str) -> bytes:
    """묶음의 고정 파일 하나를 읽어 크기·SHA 를 상수와 대조한 바이트를 돌려준다(검증한 바이트를 그대로 로드에 쓴다)."""
    root = bundle_dir()
    if root is None:
        raise BundleError("bundle_not_configured")
    m = read_bundle_manifest(root)
    want = MODEL_FILES[role]
    try:
        with open(root / m["files"][role]["name"], "rb") as f:
            data = f.read(want["bytes"] + 1)
    except OSError:
        raise BundleError("bundle_file_missing") from None
    if len(data) != want["bytes"] or hashlib.sha256(data).hexdigest() != want["sha256"]:
        raise BundleError("bundle_file_mismatch")
    return data


def bundle_file_path(role: str) -> str:
    """YOLO 처럼 경로로만 읽는 로더용: 지문을 확인한 묶음 파일의 경로."""
    read_bundle_bytes(role)
    root = bundle_dir()
    return str(root / read_bundle_manifest(root)["files"][role]["name"])


# ── 장 선택 ──────────────────────────────────────────────────────────
def select_middle(unique: list):
    """unique: 중복 제거 후 [(sequence, gray)] (sequence 오름차순). 15장 미만이면 None. 반환 (시작 index, 선택 15장)."""
    n = len(unique)
    if n < MIN_UNIQUE:
        return None
    if n > MAX_UNIQUE:
        raise ValueError("too_many")
    c = n // 2
    s = max(0, min(n - K, c - K // 2))
    return s, list(unique[s:s + K])


# ── YOLO 원좌표 검사와 크롭(원 실험 crop_lib 과 같은 계산) ───────────────
def _r9(v: float) -> float:
    return round(float(v), 9)


def validate_raw(raw, conf) -> dict:
    """원 xywh(정규화)·확신도를 자르기/반올림 전에 검사. 어긋나면 invalid_box(그 장만 제외)."""
    try:
        xc, yc, w, h = (float(v) for v in raw)
        c = float(conf)
    except (TypeError, ValueError):
        return {"status": "invalid_box"}
    if not all(math.isfinite(v) for v in (xc, yc, w, h, c)) or w <= 0 or h <= 0 or not (DET_CONF_MIN - 1e-9 <= c <= DET_CONF_MAX + 1e-9):
        return {"status": "invalid_box"}
    tol = DET_EDGE_TOL
    if xc - w / 2 < -tol or yc - h / 2 < -tol or xc + w / 2 > 1 + tol or yc + h / 2 > 1 + tol:
        return {"status": "invalid_box"}
    return {"status": "found", "raw": [xc, yc, w, h], "conf": c}


def crop_geometry(raw, W: int, H: int) -> dict:
    xc, yc, w, h = raw
    side = max(CROP_MIN_SIDE, int(math.ceil(_r9(CROP_SCALE * max(w * W, h * H)))))
    left = int(math.floor(_r9(xc * W - side / 2)))
    top = int(math.floor(_r9(yc * H - side / 2)))
    ox0, oy0, ox1, oy1 = max(0, left), max(0, top), min(W, left + side), min(H, top + side)
    overlap = max(0, ox1 - ox0) * max(0, oy1 - oy0)
    vf = overlap / float(side * side)
    return {"side": side, "left": left, "top": top, "src_rect": [ox0, oy0, ox1, oy1], "valid_fraction": round(vf, 6),
            "ok": vf >= CROP_VALID_FRACTION_MIN}


def make_crop(gray: np.ndarray, geom: dict) -> np.ndarray:
    s, l, t = geom["side"], geom["left"], geom["top"]
    ox0, oy0, ox1, oy1 = geom["src_rect"]
    out = np.zeros((s, s), np.uint8)
    if ox1 > ox0 and oy1 > oy0:
        out[oy0 - t:oy1 - t, ox0 - l:ox1 - l] = gray[oy0:oy1, ox0:ox1]
    return out


def preprocess_crop(crop_u8: np.ndarray):
    import torch
    import torch.nn.functional as F

    v = torch.from_numpy(np.ascontiguousarray(crop_u8).astype("float32") / 255.0)[None, None]
    v = F.interpolate(v, size=[G, G], mode="bilinear", align_corners=False, antialias=True)[0, 0]
    x = v[None].repeat(3, 1, 1)
    return (x - torch.tensor(IMAGENET_MEAN).view(3, 1, 1)) / torch.tensor(IMAGENET_STD).view(3, 1, 1)


def crops_tensor(crops: list):
    import torch

    xs = [preprocess_crop(c["u8"]) for c in crops[:KMAX]]
    mask = torch.zeros(KMAX)
    mask[:len(xs)] = 1
    while len(xs) < KMAX:
        xs.append(torch.zeros(3, G, G))
    return torch.stack(xs), mask


def required_valid(n: int) -> int:
    return max(VALID_CROP_MIN, int(math.ceil(_r9(VALID_CROP_FRACTION_MIN * n))))


def series_crops(grays: list, detect_raw) -> dict:
    """grays: 선택 15장(uint8). detect_raw(gray) → {'status':'not_found'} 또는 {'status':'found'|'invalid_box', raw, conf}."""
    recs, crops = [], []
    for j, g in enumerate(grays):
        det = detect_raw(g)
        st = det.get("status")
        rec = {"index": j, "status": st if st in FRAME_STATUSES else "invalid_box"}
        if st == "found":
            H, W = g.shape
            geom = crop_geometry(det["raw"], W, H)
            rec["raw"], rec["conf"] = det["raw"], det["conf"]
            rec["geom"] = {k: geom[k] for k in ("side", "left", "top", "src_rect", "valid_fraction")}
            if geom["ok"]:
                crops.append({"index": j, "u8": make_crop(g, geom), "geom": geom})
            else:
                rec["status"] = "crop_rejected"
        recs.append(rec)
    req = required_valid(len(grays))
    return {"status": "ok" if len(crops) >= req else "insufficient", "crops": crops, "recs": recs, "n_valid": len(crops), "required": req}


# ── 고정 결합 ────────────────────────────────────────────────────────
def logit(p: float) -> float:
    if not (0.0 < p < 1.0):
        raise ValueError("logit 범위")
    return math.log(p) - math.log1p(-p)


def stable_sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def combine(z_legacy, z_v4):
    """(mE, decision_score) 또는 None. 입력이 None·bool·비유한이면 None(대체 없음)."""
    for z in (z_legacy, z_v4):
        if type(z) is not float or not math.isfinite(z):
            return None
    mE = ALPHA * (z_legacy - logit(TAU_L)) + (1.0 - ALPHA) * (z_v4 - logit(TAU_V))
    if not math.isfinite(mE):
        return None
    score = stable_sigmoid(mE)
    if not (math.isfinite(score) and 0.0 <= score <= 1.0):
        return None
    return mE, score


def reference_position(score: float) -> str:
    return "above" if score >= TAU_E else "below"     # 반올림 전 원 정밀도, 허용오차 없음


# ── v4 모델 ──────────────────────────────────────────────────────────
def build_v4_model():
    """ResNet18 encoder(fc=Identity, 사전학습 다운로드 없음) + 유효 크롭 masked mean⊕max + Linear(1024,1)."""
    import torch
    import torch.nn as nn
    import torchvision

    class SeriesNet(nn.Module):
        def __init__(self):
            super().__init__()
            m = torchvision.models.resnet18(weights=None)
            m.fc = nn.Identity()
            self.encoder = m
            self.head = nn.Linear(1024, 1)

        def forward(self, x, mask):
            b, k = x.shape[:2]
            valid = mask.bool()
            if (valid.sum(1) == 0).any():
                raise ValueError("유효 장이 0인 시리즈")
            feats = self.encoder(x[valid])
            f = feats.new_zeros(b, k, feats.shape[1])
            f[valid] = feats
            m = valid.to(f.dtype).unsqueeze(-1)
            mean = (f * m).sum(1) / m.sum(1)
            mx = f.masked_fill(~valid.unsqueeze(-1), float("-inf")).max(1).values
            return self.head(torch.cat([mean, mx], 1)).squeeze(1)

    return SeriesNet()


def _all_finite(state: dict) -> bool:
    import torch

    return all(bool(torch.isfinite(v).all()) for v in state.values() if torch.is_tensor(v) and v.is_floating_point())


class V4Model:
    def __init__(self) -> None:
        self.model = None
        self.device = "cpu"
        self.model_sha256 = MODEL_FILES["v4"]["sha256"]
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        with self._lock:
            if self.model is not None:
                return
            try:
                data = read_bundle_bytes("v4")
            except BundleError as exc:
                raise EnsembleUnavailable("v4_" + exc.reason) from None
            try:
                import torch

                ck = torch.load(io.BytesIO(data), map_location="cpu", weights_only=True)
                if not isinstance(ck, dict) or not isinstance(ck.get("model"), dict) or not _all_finite(ck["model"]):
                    raise ValueError("bad checkpoint")
                m = build_v4_model()
                m.load_state_dict(ck["model"], strict=True)
                m.to("cpu").eval()
            except Exception:
                raise EnsembleUnavailable("v4_load_failed") from None
            self.model = m

    def logit(self, crops: list) -> float:
        import torch

        from .model import INFERENCE_LOCK

        self.load()
        x, mask = crops_tensor(crops)
        with INFERENCE_LOCK, torch.inference_mode(), torch.autocast(device_type="cpu", enabled=False):
            z = float(self.model(x[None], mask[None]).float().reshape(-1)[0].item())
        if not math.isfinite(z):
            raise EnsembleUnavailable("v4_nonfinite_logit")
        return z


v4_model = V4Model()


# ── 한 요청 계산 ──────────────────────────────────────────────────────
def _legacy():
    from .acl_volume import acl_volume

    return acl_volume


def _detector():
    from .screen_localization import locator

    return locator


def preflight() -> None:
    """세 모델이 고정 지문으로 준비됐는지(로드 포함). 실패하면 EnsembleUnavailable.

    순서(ENSEMBLE-LOCAL-001): ① 로컬 묶음 설정·manifest(요청마다) → ② 아직 안 읽은 모델이 있으면 세 파일 지문 전부 →
    ③ v4 → YOLO → B 로드. 묶음이 없거나 무효면 ①·②에서 닫혀, B 의 HF 다운로드 경로를 부르지 않는다.
    (묶음이 설정된 프로세스에서는 B·YOLO 도 묶음에서만 읽는다 — acl_volume·screen_localization.)
    """
    from .errors import KneeModelNotReady
    from .screen_localization import LocationUnavailable

    check_bundle_config()
    b, det = _legacy(), _detector()
    if not (v4_model.ready and getattr(b, "ready", False) and getattr(det, "ready", False)):
        check_bundle_files()
    v4_model.load()
    try:
        det.ensure_raw_ready()
    except LocationUnavailable as exc:
        raise EnsembleUnavailable("yolo_" + exc.reason) from None
    try:
        b.load()
    except KneeModelNotReady:
        raise EnsembleUnavailable("legacy_unavailable") from None
    if b.model_sha256 != MODEL_FILES["legacy"]["sha256"]:
        raise EnsembleUnavailable("legacy_mismatch")
    if b.device != "cpu" or b.crop is not None or getattr(b, "pool", "max") != "max" or getattr(b, "backbone", "resnet18") != "resnet18":
        raise EnsembleUnavailable("legacy_config_mismatch")


def analyze(unique: list) -> dict:
    """unique: 서버가 디코드·중복 제거를 마친 [(sequence, gray uint8)]. 반환 계산 결과(dict).

    status: ok(점수 있음) / unavailable(구조상 정상이지만 앙상블을 못 냄 → 200 not_evaluated).
    모델 부재·지문·로드·검출 내부 오류는 EnsembleUnavailable 로 올린다(→ 503).
    """
    from .screen_localization import LocationUnavailable

    sel = select_middle(unique)
    base = {"unique_count": len(unique), "used_count": 0, "used_sequences": [], "selection_start": None,
            "n_valid": None, "required": required_valid(K), "frames": [], "middle": None}
    if sel is None:
        return {**base, "status": "unavailable", "reason": "insufficient_ensemble_frames", "score": None, "margin": None}
    start, chosen = sel
    seqs = [s for s, _ in chosen]
    grays = [np.ascontiguousarray(g) for _, g in chosen]
    if any(g.dtype != np.uint8 or g.ndim != 2 for g in grays):
        raise EnsembleUnavailable("input_not_uint8")
    base.update(used_count=K, used_sequences=seqs, selection_start=start)
    preflight()
    det = _detector()
    try:
        sc = series_crops(grays, det.detect_raw)
    except LocationUnavailable as exc:
        raise EnsembleUnavailable("yolo_" + exc.reason) from None
    frames = [{"ordinal": r["index"] + 1, "sequence": seqs[r["index"]], "status": r["status"]} for r in sc["recs"]]
    middle = {"index": MIDDLE_INDEX, "sequence": seqs[MIDDLE_INDEX], "gray": grays[MIDDLE_INDEX], "rec": sc["recs"][MIDDLE_INDEX]}
    base.update(n_valid=sc["n_valid"], required=sc["required"], frames=frames, middle=middle)
    if sc["status"] != "ok":
        return {**base, "status": "unavailable", "reason": "insufficient_valid_crops", "score": None, "margin": None}
    try:
        z_l = _legacy().logit(grays)
    except Exception:
        raise EnsembleUnavailable("legacy_inference_failed") from None
    z_v = v4_model.logit(sc["crops"])
    c = combine(z_l, z_v)
    if c is None:
        raise EnsembleUnavailable("nonfinite_combination")
    m_e, score = c
    return {**base, "status": "ok", "reason": None, "score": score, "margin": m_e, "position": reference_position(score)}
