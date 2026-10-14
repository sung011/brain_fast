"""
흉부 X-ray 앙상블 분류 서비스.

여러 timm 체크포인트를 불러와 확률을 평균한다 (다중 라벨, sigmoid).
가중치: Hugging Face THORAX_HF_REPO 의 THORAX_CKPT_FILES
설정이 비어 있으면 load() 가 "흉부 모델 준비 중" 오류를 낸다.
"""
from __future__ import annotations

from pathlib import Path

import albumentations as A
import numpy as np
import torch
import timm
from albumentations.pytorch import ToTensorV2
from huggingface_hub import hf_hub_download

from . import config
from .constants import CLASSES

# ImageNet 정규화 + 고정 리사이즈. 팀 모델의 학습 전처리와 다르면 여기를 맞춘다.
_infer_transforms = A.Compose(
    [
        A.Resize(config.IMG_SIZE, config.IMG_SIZE),
        A.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
        ToTensorV2(),
    ]
)

NOT_CONFIGURED = (
    "흉부 X-ray 모델이 아직 준비되지 않았습니다. "
    ".env 에 THORAX_HF_REPO 와 THORAX_CKPT_FILES 를 넣으세요."
)


class ThoraxModelNotReady(RuntimeError):
    """흉부 모델 가중치 위치가 설정되지 않았다."""


class ThoraxEnsemble:
    """흉부 X-ray 다중 라벨 앙상블."""

    task = "thorax"

    def __init__(self, ckpt_files: list[str] | None = None, classes: list[str] | None = None) -> None:
        self.ckpt_files = list(ckpt_files if ckpt_files is not None else config.CKPT_FILES)
        self.classes = list(classes if classes is not None else CLASSES)
        self.device = self._get_device()
        self.models: list[torch.nn.Module] = []
        self.model_names: list[str] = []
        self.ready = False
        self.error: str | None = None

    @property
    def num_classes(self) -> int:
        return len(self.classes)

    @staticmethod
    def _get_device() -> torch.device:
        """CUDA → MPS(Apple) → CPU 순으로 디바이스 선택."""
        if torch.cuda.is_available():
            return torch.device("cuda")
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")

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
    def _infer_num_classes(sd: dict, fallback: int) -> int:
        for cand in ("head.fc.weight", "classifier.weight", "fc.weight", "head.weight"):
            if cand in sd and hasattr(sd[cand], "shape") and sd[cand].ndim >= 1:
                return int(sd[cand].shape[0])
        return fallback

    @staticmethod
    def _infer_model_name(blob: dict, path: Path) -> str:
        """체크포인트의 model_name 을 쓰고, 없으면 파일 이름으로 timm 모델명을 고른다."""
        if blob.get("model_name"):
            return str(blob["model_name"])
        stem = path.stem.lower()
        if "densenet121" in stem:
            return "densenet121"
        if "resnet50" in stem:
            return "resnet50"
        if "resnet18" in stem:
            return "resnet18"
        if "convnext" in stem:
            return "convnext_base.fb_in22k_ft_in1k_384"
        if "effv2" in stem or "efficientnet" in stem:
            return "tf_efficientnetv2_m.in21k_ft_in1k"
        raise ValueError(
            f"{path.name}: 모델 이름을 알 수 없습니다. 체크포인트에 model_name 을 넣어 주세요."
        )

    def _download(self) -> list[Path]:
        return [
            Path(
                hf_hub_download(
                    repo_id=config.HF_REPO,
                    filename=fname,
                    token=config.HF_TOKEN or None,
                )
            )
            for fname in self.ckpt_files
        ]

    def load(self) -> None:
        if self.ready:
            return
        if not (config.HF_REPO and self.ckpt_files):
            self.error = NOT_CONFIGURED
            raise ThoraxModelNotReady(NOT_CONFIGURED)
        try:
            self.load_paths(self._download())
        except Exception as exc:
            self.ready = False
            self.error = str(exc)
            raise

    def load_paths(self, ckpt_paths: list[Path]) -> None:
        """내려받은 체크포인트 파일들로 모델을 만든다."""
        models: list[torch.nn.Module] = []
        names: list[str] = []
        outputs: list[int] = []
        resolved_classes: list[str] | None = None
        for path in ckpt_paths:
            blob = torch.load(path, map_location=self.device, weights_only=False)
            if not isinstance(blob, dict):
                raise TypeError(f"예상치 못한 체크포인트 타입: {type(blob)}")
            if resolved_classes is None and isinstance(blob.get("classes"), list) and blob["classes"]:
                resolved_classes = [str(c) for c in blob["classes"]]
            name = self._infer_model_name(blob, path)
            sd = self._extract_state_dict(blob)
            n_cls = self._infer_num_classes(sd, len(resolved_classes or self.classes))
            model = timm.create_model(name, pretrained=False, num_classes=n_cls).to(self.device)
            model.load_state_dict(sd)
            model.eval()
            models.append(model)
            names.append(name)
            outputs.append(n_cls)

        classes = resolved_classes or self.classes
        if any(n != len(classes) for n in outputs):
            raise ValueError(
                f"체크포인트 출력 수 {outputs} 와 클래스 수 {len(classes)} 가 다릅니다. "
                "체크포인트의 classes 목록이나 constants.CLASSES 를 확인하세요."
            )
        self.classes = classes
        self.models = models
        self.model_names = names
        self.ready = True
        self.error = None

    @torch.no_grad()
    def predict(self, img_uint8: np.ndarray) -> np.ndarray:
        if not self.ready:
            raise RuntimeError(self.error or "흉부 앙상블이 로드되지 않았습니다.")
        x = _infer_transforms(image=img_uint8)["image"].unsqueeze(0).to(self.device)
        all_prob = None
        for model in self.models:
            logits = model(x)
            if config.USE_TTA_FLIP:
                logits = (logits + model(torch.flip(x, dims=[-1]))) / 2
            prob = torch.sigmoid(logits)[0].cpu().numpy()
            all_prob = prob if all_prob is None else all_prob + prob
        return all_prob / len(self.models)


# 싱글톤 — thorax 라우터와 ROI 채점에서 공유
ensemble_service = ThoraxEnsemble()
