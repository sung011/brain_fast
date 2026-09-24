"""
뇌 CT 앙상블 분류 서비스.

출혈(ICH, 6클래스) + germinoma(tumor, 1클래스)를 각각
ConvNeXt-Base + EfficientNetV2-M 로 로드하고, 분석 시 둘 다 추론한다.

가중치: HuggingFace kimsungil/Brain_Hemorrhage
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
    ENABLE_GERMINOMA,
    GERMINOMA_CKPT_FILES,
    HF_REPO,
    IMG_SIZE,
    USE_TTA_FLIP,
)
from .constants import CLASSES, GERMINOMA_CLASSES

# ImageNet 정규화 + 고정 리사이즈 (학습 파이프라인과 동일)
_infer_transforms = A.Compose(
    [
        A.Resize(IMG_SIZE, IMG_SIZE),
        A.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
        ToTensorV2(),
    ]
)


class TaskEnsemble:
    """단일 태스크(출혈 또는 germinoma) ConvNeXt + EffV2 앙상블."""

    def __init__(
        self,
        task: str,
        ckpt_files: list[str],
        classes: list[str],
        *,
        apply_any_mode: bool = False,
    ) -> None:
        self.task = task
        self.ckpt_files = list(ckpt_files)
        self.classes = list(classes)
        self.apply_any_mode = apply_any_mode
        self.device = EnsembleService._get_device()
        self.models: list[torch.nn.Module] = []
        self.model_names: list[str] = []
        self.ready = False
        self.error: str | None = None

    @property
    def num_classes(self) -> int:
        return len(self.classes)

    def load(self) -> None:
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
                for fname in self.ckpt_files
            ]
            models: list[torch.nn.Module] = []
            names: list[str] = []
            resolved_classes: list[str] | None = None

            for path in ckpt_paths:
                blob = torch.load(path, map_location=self.device, weights_only=False)
                if not isinstance(blob, dict):
                    raise TypeError(f"예상치 못한 체크포인트 타입: {type(blob)}")
                if resolved_classes is None and isinstance(blob.get("classes"), list) and blob["classes"]:
                    resolved_classes = [str(c) for c in blob["classes"]]
                name = EnsembleService._infer_model_name(blob, path)
                sd = EnsembleService._extract_state_dict(blob)
                n_cls = EnsembleService._infer_num_classes(sd, resolved_classes or self.classes)
                model = EnsembleService._make_model(name, n_cls).to(self.device)
                model.load_state_dict(sd)
                model.eval()
                models.append(model)
                names.append(name)

            if resolved_classes:
                self.classes = resolved_classes
            self.models = models
            self.model_names = names
            self.ready = True
            self.error = None
        except Exception as exc:
            self.ready = False
            self.error = str(exc)
            raise

    @torch.no_grad()
    def predict(self, img_uint8: np.ndarray) -> np.ndarray:
        if not self.ready:
            raise RuntimeError(self.error or f"{self.task} 앙상블이 로드되지 않았습니다.")
        x = _infer_transforms(image=img_uint8)["image"].unsqueeze(0).to(self.device)
        all_prob = None
        for model in self.models:
            logits = model(x)
            if USE_TTA_FLIP:
                logits = (logits + model(torch.flip(x, dims=[-1]))) / 2
            prob = torch.sigmoid(logits)[0].cpu().numpy()
            all_prob = prob if all_prob is None else all_prob + prob
        probs = all_prob / len(self.models)
        if self.apply_any_mode and ANY_MODE == "max5" and len(probs) >= 6:
            probs = probs.copy()
            probs[5] = float(np.max(probs[:5]))
        return probs


class EnsembleService:
    """출혈 + germinoma 듀얼 앙상블 로드·추론."""

    def __init__(self) -> None:
        self.hemorrhage = TaskEnsemble(
            "hemorrhage",
            CKPT_FILES,
            CLASSES,
            apply_any_mode=True,
        )
        self.germinoma: TaskEnsemble | None = None
        if ENABLE_GERMINOMA:
            self.germinoma = TaskEnsemble(
                "germinoma",
                GERMINOMA_CKPT_FILES,
                GERMINOMA_CLASSES,
                apply_any_mode=False,
            )

    @property
    def device(self) -> torch.device:
        return self.hemorrhage.device

    @property
    def models(self) -> list[torch.nn.Module]:
        """하위 호환 — 출혈 모델 목록."""
        return self.hemorrhage.models

    @property
    def model_names(self) -> list[str]:
        return self.hemorrhage.model_names

    @property
    def ready(self) -> bool:
        if not self.hemorrhage.ready:
            return False
        if self.germinoma is not None and not self.germinoma.ready:
            return False
        return True

    @property
    def error(self) -> str | None:
        if self.hemorrhage.error:
            return self.hemorrhage.error
        if self.germinoma is not None and self.germinoma.error:
            return self.germinoma.error
        return None

    @staticmethod
    def _get_device() -> torch.device:
        """CUDA → MPS(Apple) → CPU 순으로 디바이스 선택."""
        if torch.cuda.is_available():
            return torch.device("cuda")
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")

    @staticmethod
    def _make_model(name: str, num_classes: int) -> torch.nn.Module:
        """timm 모델 생성 — ResNet은 dropout 미적용."""
        kwargs: dict = dict(pretrained=False, num_classes=num_classes)
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
    def _infer_num_classes(sd: dict, fallback_classes: list[str]) -> int:
        for cand in ("head.fc.weight", "classifier.weight", "fc.weight", "head.weight"):
            if cand in sd and hasattr(sd[cand], "shape") and sd[cand].ndim >= 1:
                return int(sd[cand].shape[0])
        return len(fallback_classes)

    @staticmethod
    def _infer_model_name(blob: dict, path: Path) -> str:
        """체크포인트 메타데이터 또는 weight 키 패턴으로 timm 모델명 추론."""
        if blob.get("model_name"):
            return blob["model_name"]
        stem = path.stem.lower()
        if "resnet18" in stem or "ich_resnet" in stem:
            return "resnet18"
        if "convnext_base" in stem or "convnext" in stem:
            return "convnext_base.fb_in22k_ft_in1k_384"
        if "effv2" in stem or "efficientnetv2" in stem:
            return "tf_efficientnetv2_m.in21k_ft_in1k"
        if "efficientnet" in stem:
            return "tf_efficientnetv2_m.in21k_ft_in1k"
        sd = EnsembleService._extract_state_dict(blob)
        keys = list(sd.keys())
        if any(k.startswith("stages.") for k in keys):
            return "convnext_base.fb_in22k_ft_in1k_384"
        if any(k.startswith("conv_stem") for k in keys):
            return "tf_efficientnetv2_m.in21k_ft_in1k"
        if any(k.startswith("layer1.") for k in keys) and "fc.weight" in sd:
            return "resnet18"
        return "tf_efficientnetv2_m.in21k_ft_in1k"

    def load(self) -> None:
        """HF에서 출혈·germinoma 체크포인트 다운로드 후 eval 모드로 로드."""
        if self.ready:
            return
        self.hemorrhage.load()
        if self.germinoma is not None:
            self.germinoma.load()

    @torch.no_grad()
    def predict(self, img_uint8: np.ndarray) -> np.ndarray:
        """하위 호환 — 출혈 6클래스 확률만 반환."""
        return self.hemorrhage.predict(img_uint8)

    @torch.no_grad()
    def predict_all(self, img_uint8: np.ndarray) -> dict[str, np.ndarray]:
        """출혈 + germinoma 확률을 함께 반환."""
        out: dict[str, np.ndarray] = {
            "hemorrhage": self.hemorrhage.predict(img_uint8),
        }
        if self.germinoma is not None and self.germinoma.ready:
            out["germinoma"] = self.germinoma.predict(img_uint8)
        return out


# 싱글톤 — main.py lifespan과 analyze 라우터에서 공유
ensemble_service = EnsembleService()
