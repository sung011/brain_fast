"""
'선택 영역 분석' 한 장(single) 전용 ACL 시상면 2D 모델 — MSN-20261021-001 SINGLE_2D_CONNECTION_PLAN 4·5장.

- 사용자 Colab 학습 반입본 v1(ResNet18, 출력 logit 1개) **하나만** 처음 쓸 때 불러온다(lazy). 볼륨 모델·전체 무릎 앙상블·YOLO·뇌 모델은 불러오지 않는다.
- 모델 폴더는 서버 설정 KNEE_SINGLE_BUNDLE_DIR 로만 정한다(요청에서 경로·파일·프로필을 받지 않음). 비어 있으면 준비 안 됨(503).
- 읽기 전에 파일 이름·크기 상한을 보고, 읽은 바이트 그대로 SHA-256 을 코드에 고정한 값과 비교한 뒤에만 불러온다.
  torch.load(weights_only=True)·코드에 정의한 resnet18(weights=None)+Linear(512,1)·strict·유한값 확인. ZIP 안의 코드를 실행하거나 새 가중치를 내려받지 않는다.
- 전처리는 반입본(preprocess.json)과 Colab 학습 라이브러리의 preprocess 를 그대로 따른다:
  회색 uint8 → 이미지별 min-max(+1e-6) → 224×224 bilinear(antialias, align_corners=False) → 3채널 복제 → ImageNet 정규화.
  (볼륨 모델의 스택 정규화·중앙 crop·max-pool 을 쓰지 않는다. roi_crop 이면 사용자가 자른 화소를 그대로 쓴다.)
- 히트맵은 **같은 forward·같은 logit** 의 Grad-CAM(layer4). 점수만 낸 값과 히트맵을 낸 값이 같다.
- 기본 CPU. 추론은 model.INFERENCE_LOCK 으로 다른 무릎 추론과 겹치지 않게 한다.
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
from pathlib import Path

import numpy as np

from .errors import KneeAnalysisError, KneeInputError, KneeModelNotReady

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
INPUT_SIZE = 224

# 반입본 v1 고정 지문(designs/MSN-20261021-001/colab_import/v1/checksums.sha256 와 같음). 다른 파일이면 불러오지 않는다.
BUNDLE_FILES = {
    "model_state.pt": ("a34193b41fd0a902bd46fa47cc4dbed0e2a454f275e422474075727afd925199", 64 * 1024 * 1024),
    "preprocess.json": ("d0af1115d6debfb10ae517db46c0eb7f91a0bce01e7f40b65b14ded39f00bf17", 64 * 1024),
    "model_manifest.json": ("dd37e8824be64f35ebab2507bb316c74528595b833bb5c5334865b4cee11593a", 64 * 1024),
    "report.json": ("1dd489d06d1dd2d979ee25e196381aaf38e91796ac07be0ce91fdccdb2249ab6", 256 * 1024),
}
SAMPLES_SHA256 = "2faa71656d334d959482e5f0449ff942ab86dc013a7ff44b5862ec0ee9cf41b2"
PROTOCOL_SHA256 = "cd48f003419d7f1fa936c3861da6d720b3d95e48821d67b92c1cec044ba8f7e2"   # 원 manifest 가 쓴 정렬 JSON 지문
LABEL_MAP = {"0": "negative", "1": "positive", "2": "positive"}   # 원 ACL 등급 → 학습 라벨(protocol 의 매핑)


class AclSingleModel:
    def __init__(self) -> None:
        self.model = None
        self.device = "cpu"
        self.model_id = None
        self.model_sha256 = None
        self._lock = threading.Lock()

    @staticmethod
    def bundle_dir() -> str:
        from .config import get_setting

        return get_setting("KNEE_SINGLE_BUNDLE_DIR", "")

    def configured(self) -> bool:
        return bool(self.bundle_dir())

    @property
    def ready(self) -> bool:
        return self.model is not None

    @staticmethod
    def _read_pinned(root: Path, name: str) -> bytes:
        sha, limit = BUNDLE_FILES[name]
        p = root / name
        if not p.is_file() or p.is_symlink() or p.stat().st_size > limit:
            raise ValueError(name)
        data = p.read_bytes()
        if len(data) > limit or hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(name)
        return data

    def load(self) -> None:
        with self._lock:
            if self.model is not None:
                return
            try:
                import torch
                import torch.nn as nn
                from torchvision.models import resnet18

                configured = self.bundle_dir()
                root = Path(configured)
                if not configured or not root.is_dir():
                    raise ValueError("bundle")
                files = {n: self._read_pinned(root, n) for n in BUNDLE_FILES}
                man = json.loads(files["model_manifest.json"])
                pre = json.loads(files["preprocess.json"])
                if (man.get("arch") != "torchvision.resnet18" or man.get("head") != "Linear(512,1)"
                        or man.get("model_state_sha256") != BUNDLE_FILES["model_state.pt"][0]
                        or man.get("data_pack_samples_sha256") != SAMPLES_SHA256 or man.get("protocol_sha256") != PROTOCOL_SHA256):
                    raise ValueError("manifest")
                if (tuple(pre.get("imagenet_mean", ())) != IMAGENET_MEAN or tuple(pre.get("imagenet_std", ())) != IMAGENET_STD
                        or "224" not in str(pre.get("resize", ""))):
                    raise ValueError("preprocess")
                sd = torch.load(io.BytesIO(files["model_state.pt"]), map_location="cpu", weights_only=True)
                m = resnet18(weights=None)
                m.fc = nn.Linear(512, 1)
                m.load_state_dict(sd, strict=True)
                if tuple(m.fc.weight.shape) != (1, 512) or not all(torch.isfinite(t).all() for t in m.state_dict().values() if t.is_floating_point()):
                    raise ValueError("weights")
                m.eval()
            except Exception:
                # 경로·원 예외·파일 내용은 밖으로 내보내지 않는다(라우터는 고정 문구 503).
                raise KneeModelNotReady("한 장 분석 모델을 준비하지 못했습니다.") from None
            self.model, self.device = m, "cpu"
            self.model_sha256 = BUNDLE_FILES["model_state.pt"][0]
            self.model_id = f"knee_acl_single_v1@{self.model_sha256[:12]}"

    @staticmethod
    def preprocess(gray: np.ndarray):
        """회색 uint8 (H,W) → ((3,224,224) 모델 입력, (224,224) 0~1 바탕 그림). Colab 라이브러리 preprocess 와 같은 계산."""
        import torch
        import torch.nn.functional as F

        if not isinstance(gray, np.ndarray) or gray.ndim != 2 or gray.size == 0 or min(gray.shape) < 2:
            raise KneeInputError("한 장 입력은 (높이, 너비) 회색 영상이어야 합니다.")
        v = torch.from_numpy(np.ascontiguousarray(gray).astype("float32"))
        v = (v - v.min()) / (v.max() - v.min() + 1e-6)
        v = F.interpolate(v[None, None], size=[INPUT_SIZE, INPUT_SIZE], mode="bilinear", align_corners=False, antialias=True)[0]
        shown = v[0].clamp(0, 1).numpy().copy()
        v = v.repeat(3, 1, 1)
        mean = torch.tensor(IMAGENET_MEAN).view(3, 1, 1)
        std = torch.tensor(IMAGENET_STD).view(3, 1, 1)
        return (v - mean) / std, shown

    def logits(self, grays: list[np.ndarray]) -> list[float]:
        """여러 장을 한 batch 로(시험·대조용). 각 장은 서로 영향을 주지 않는다."""
        import torch

        from .model import INFERENCE_LOCK

        self.load()
        x = torch.stack([self.preprocess(g)[0] for g in grays])
        with INFERENCE_LOCK, torch.no_grad():
            return self.model(x).squeeze(1).float().tolist()

    def analyze(self, grays: list[np.ndarray], *, include_overlay: bool = True) -> dict:
        """grays: 정확히 1장. 반환: raw 점수·logit·CAM 대상 index(0)·오버레이(PIL 또는 None)."""
        import torch
        import torch.nn.functional as F

        from .localization import Localization
        from .model import INFERENCE_LOCK

        if len(grays) != 1:
            raise KneeInputError("한 장 모델은 정확히 1장을 받습니다.")
        self.load()
        x, shown = self.preprocess(grays[0])
        x = x[None]
        if not include_overlay:
            with INFERENCE_LOCK, torch.no_grad():
                logit = float(self.model(x).squeeze())
            return {"raw_score": _prob(logit), "logit": logit, "cam_index": None, "overlay": None, "activated": False}
        acts: dict = {}

        def _hook(_m, _i, out):
            out.retain_grad()
            acts["A"] = out

        with INFERENCE_LOCK:
            handle = self.model.layer4.register_forward_hook(_hook)
            try:
                self.model.zero_grad(set_to_none=True)
                with torch.enable_grad():
                    out = self.model(x).squeeze()   # 이 logit 하나로 점수와 히트맵을 함께 만든다
                    out.backward()
                a = acts["A"]
                if a.grad is None:
                    raise KneeAnalysisError("Grad-CAM 기울기를 얻지 못했습니다.")
                cam = F.relu((a.grad.mean(dim=(2, 3), keepdim=True) * a).sum(1))
                logit = float(out.detach())
            finally:
                handle.remove()
                acts.clear()
                self.model.zero_grad(set_to_none=True)
        cam = F.interpolate(cam.unsqueeze(1), size=(INPUT_SIZE, INPUT_SIZE), mode="bilinear", align_corners=False)[0, 0].detach()
        cam = (cam / (cam.max() + 1e-6)).numpy()
        if not np.isfinite(cam).all():
            raise KneeAnalysisError("Grad-CAM 결과에 NaN/Inf 가 있습니다.")
        image, activated = Localization.build_overlay(shown, cam)
        return {"raw_score": _prob(logit), "logit": logit, "cam_index": 0, "overlay": image, "activated": bool(activated)}


def _prob(logit: float) -> float:
    if not np.isfinite(logit):
        raise KneeAnalysisError("모델 점수가 올바르지 않습니다.")
    p = float(1.0 / (1.0 + np.exp(-np.clip(logit, -60.0, 60.0))))
    if not (0.0 <= p <= 1.0):
        raise KneeAnalysisError("모델 점수가 올바르지 않습니다.")
    return p


acl_single = AclSingleModel()
