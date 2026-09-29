"""
무릎 MRI 전처리.

학습 때와 똑같은 방식으로 볼륨(슬라이스 묶음)을 모델 입력으로 바꾼다.
입력 (S,H,W) numpy → 출력 (S,3,IMG_SIZE,IMG_SIZE) torch, ImageNet 정규화.
모델에 넣기 전에 validate_volume 으로 모양·자료형·크기·NaN 을 먼저 거른다.
"""
from __future__ import annotations

import numpy as np
import torch
import torchvision.transforms.functional as TF

from .config import IMG_SIZE, MAX_SIDE, MAX_SLICES, MAX_VOXELS
from .errors import KneeInputError

IMAGENET_MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
IMAGENET_STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


class Preprocess:
    @staticmethod
    def validate_volume(vol) -> np.ndarray:
        """볼륨이 (S,H,W) 실수/정수 숫자 배열이고, 비어 있지 않고, 유한하고, 한도 안인지 확인한다."""
        if not isinstance(vol, np.ndarray):
            raise KneeInputError("볼륨은 numpy 배열이어야 합니다.")
        if vol.dtype.kind not in "uif":
            raise KneeInputError(f"숫자(정수/실수) 자료형이 아닙니다: {vol.dtype}")
        if vol.ndim != 3:
            raise KneeInputError(f"볼륨은 (슬라이스, 높이, 너비) 3차원이어야 합니다. 받은 모양: {vol.shape}")
        s, h, w = vol.shape
        if min(s, h, w) <= 0:
            raise KneeInputError(f"크기가 0인 축이 있습니다: {vol.shape}")
        if s > MAX_SLICES or h > MAX_SIDE or w > MAX_SIDE or vol.size > MAX_VOXELS:
            raise KneeInputError(f"볼륨이 너무 큽니다: {vol.shape}")
        if vol.dtype.kind == "f" and not np.isfinite(vol).all():
            raise KneeInputError("NaN 또는 Inf 값이 들어 있습니다.")
        return vol

    @staticmethod
    def to_unit(vol: np.ndarray, img_size: int = IMG_SIZE, crop: float | None = None) -> torch.Tensor:
        """(S,H,W) → (S,1,img,img), 0~1 범위. 볼륨 전체 기준 min-max.

        crop 이 0~1 사이면 리사이즈 전에 가운데 그 비율만 남긴다(관절 확대).
        """
        v = torch.from_numpy(np.ascontiguousarray(vol).astype("float32"))
        v = (v - v.min()) / (v.max() - v.min() + 1e-6)
        v = v.unsqueeze(1)
        if crop and 0 < crop < 1:
            h, w = v.shape[-2], v.shape[-1]
            ch, cw = int(round(h * crop)), int(round(w * crop))
            top, left = (h - ch) // 2, (w - cw) // 2
            v = v[..., top : top + ch, left : left + cw]
        if v.shape[-1] != img_size or v.shape[-2] != img_size:
            v = TF.resize(v, [img_size, img_size], antialias=True)
        return v

    @staticmethod
    def preprocess_volume(vol: np.ndarray, img_size: int = IMG_SIZE, crop: float | None = None) -> torch.Tensor:
        """(S,H,W) numpy → (S,3,img,img) 정규화된 텐서. 모델에 바로 넣는다."""
        Preprocess.validate_volume(vol)
        v = Preprocess.to_unit(vol, img_size, crop).repeat(1, 3, 1, 1)
        return (v - IMAGENET_MEAN) / IMAGENET_STD

    @staticmethod
    def to_gray01(x: torch.Tensor) -> np.ndarray:
        """정규화된 (3,H,W) 슬라이스 한 장 → 0~1 회색 (H,W). 화면에 그릴 때 쓴다."""
        g = x.cpu() * IMAGENET_STD + IMAGENET_MEAN
        return g.clamp(0, 1).mean(0).numpy()
