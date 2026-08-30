"""
ICH 앙상블 분류 서비스.

EfficientNet-B4 + ConvNeXt-Small + ResNet18 세 모델의 sigmoid 출력을
평균하여 6클래스(5종 출혈 + any) 확률을 반환한다.

가중치: HuggingFace kimsungil/brain-ich-ensemble
"""
from __future__ import annotations

from pathlib import Path

import albumentations as A
import numpy as np
import torch
import timm
from albumentations.pytorch import ToTensorV2
from huggingface_hub import hf_hub_download

from .config import (
    ANY_MODE,
    CKPT_FILES,
    HF_REPO,
    IMG_SIZE,
    USE_TTA_FLIP,
)
from .constants import CLASSES

# ImageNet 정규화 + 고정 리사이즈 (학습 파이프라인과 동일)
_infer_transforms = A.Compose(
    [
        A.Resize(IMG_SIZE, IMG_SIZE),
        A.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
        ToTensorV2(),
    ]
)


class EnsembleService:
    """3모델 앙상블 로드·추론."""

    def __init__(self) -> None:
        self.device = self._get_device()
        self.models: list[torch.nn.Module] = []
        self.model_names: list[str] = []
        self.ready = False
        self.error: str | None = None

    @staticmethod
    def _get_device() -> torch.device:
        """CUDA → MPS(Apple) → CPU 순으로 디바이스 선택."""
        if torch.cuda.is_available():
            return torch.device("cuda")
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")

    @staticmethod
    def _make_model(name: str) -> torch.nn.Module:
        """timm 모델 생성 — ResNet은 dropout 미적용."""
        kwargs: dict = dict(pretrained=False, num_classes=6)
        if "resnet" not in name.lower():
            kwargs.update(drop_rate=0.2, drop_path_rate=0.1)
        return timm.create_model(name, **kwargs)

    @staticmethod
    def _extract_state_dict(blob: dict) -> dict:
        """체크포인트 dict에서 state_dict 키를 찾아 추출."""
        for key in ("model_state_dict", "state_dict", "model"):
            sd = blob.get(key)
            if isinstance(sd, dict) and sd:
                sample = next(iter(sd.values()))
                if hasattr(sample, "shape"):
                    return sd
        vals = list(blob.values())[:8]
        if vals and all(hasattr(v, "shape") for v in vals):
            return blob
        raise KeyError(f"state_dict를 찾을 수 없음. keys={list(blob.keys())}")

    @staticmethod
    def _infer_model_name(blob: dict, path: Path) -> str:
        """체크포인트 메타데이터 또는 weight 키 패턴으로 timm 모델명 추론."""
        if blob.get("model_name"):
            return blob["model_name"]
        stem = path.stem.lower()
        if "resnet18" in stem or "ich_resnet" in stem:
            return "resnet18"
        if "convnext" in stem:
            return "convnext_small.fb_in22k_ft_in1k"
        if "efficientnet" in stem:
            return "tf_efficientnet_b4.ns_jft_in1k"
        sd = EnsembleService._extract_state_dict(blob)
        keys = list(sd.keys())
        if any(k.startswith("stages.") for k in keys):
            return "convnext_small.fb_in22k_ft_in1k"
        if any(k.startswith("conv_stem") for k in keys):
            return "tf_efficientnet_b4.ns_jft_in1k"
        if any(k.startswith("layer1.") for k in keys) and "fc.weight" in sd:
            return "resnet18"
        return "tf_efficientnet_b4.ns_jft_in1k"

    def load(self) -> None:
        """HF에서 체크포인트 다운로드 후 3모델을 eval 모드로 로드."""
        if self.ready:
            return
        try:
            from .config import HF_TOKEN

            ckpt_paths = [
                Path(
                    hf_hub_download(
                        repo_id=HF_REPO,
                        filename=fname,
                        token=HF_TOKEN or None,
                    )
                )
                for fname in CKPT_FILES
            ]
            for path in ckpt_paths:
                blob = torch.load(path, map_location=self.device, weights_only=False)
                if not isinstance(blob, dict):
                    raise TypeError(f"예상치 못한 체크포인트 타입: {type(blob)}")
                name = self._infer_model_name(blob, path)
                sd = self._extract_state_dict(blob)
                model = self._make_model(name).to(self.device)
                model.load_state_dict(sd)
                model.eval()
                self.models.append(model)
                self.model_names.append(name)
            self.ready = True
            self.error = None
        except Exception as exc:
            self.ready = False
            self.error = str(exc)
            raise

    @torch.no_grad()
    def predict(self, img_uint8: np.ndarray) -> np.ndarray:
        """
        HxWx3 uint8 이미지 → 6개 클래스 sigmoid 확률 (numpy float32).

        TTA: 좌우 flip logit 평균 후 sigmoid.
        """
        if not self.ready:
            raise RuntimeError(self.error or "앙상블 모델이 로드되지 않았습니다.")
        x = _infer_transforms(image=img_uint8)["image"].unsqueeze(0).to(self.device)
        all_prob = None
        for model in self.models:
            logits = model(x)
            if USE_TTA_FLIP:
                logits = (logits + model(torch.flip(x, dims=[-1]))) / 2
            prob = torch.sigmoid(logits)[0].cpu().numpy()
            all_prob = prob if all_prob is None else all_prob + prob
        probs = all_prob / len(self.models)
        # any 클래스를 상위 5개 max로 대체하는 옵션
        if ANY_MODE == "max5":
            probs = probs.copy()
            probs[5] = float(np.max(probs[:5]))
        return probs


# 싱글톤 — main.py lifespan과 analyze.py에서 공유
ensemble_service = EnsembleService()
