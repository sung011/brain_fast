"""
무릎 MRI 분류 모델 구조 (MRNet 방식, 예측 전용).

볼륨(슬라이스 S장)을 슬라이스별로 ResNet 에 통과시킨 뒤
슬라이스 특징을 하나로 합쳐(max 또는 attention) 확률 하나(logit)를 낸다.
학습된 체크포인트의 이름(features / attn / head)과 똑같이 만들어야 불러올 수 있다.
"""
from __future__ import annotations

import threading

import torch
import torch.nn as nn
from torchvision.models import resnet18, resnet34

_BACKBONES = {"resnet18": resnet18, "resnet34": resnet34}

# 같은 모델 객체로 예측과 Grad-CAM 을 동시에 돌리면 hook·gradient 가 섞이므로 한 번에 하나만 돌린다.
INFERENCE_LOCK = threading.RLock()


class GatedAttentionPool(nn.Module):
    """(S,D) → (1,D). 슬라이스마다 중요도를 학습해 가중합한다."""

    def __init__(self, dim: int, hidden: int = 128) -> None:
        super().__init__()
        self.V = nn.Linear(dim, hidden)
        self.U = nn.Linear(dim, hidden)
        self.w = nn.Linear(hidden, 1)

    def forward(self, f: torch.Tensor) -> torch.Tensor:
        a = torch.tanh(self.V(f)) * torch.sigmoid(self.U(f))
        w = torch.softmax(self.w(a), dim=0)
        return (w * f).sum(dim=0, keepdim=True)


class MRNetModel(nn.Module):
    def __init__(self, backbone: str = "resnet18", pool: str = "max", dropout: float = 0.5) -> None:
        super().__init__()
        if backbone not in _BACKBONES:
            raise ValueError(f"backbone 은 {list(_BACKBONES)} 중 하나여야 합니다: {backbone}")
        if pool not in ("max", "attn"):
            raise ValueError(f"pool 은 max 또는 attn 이어야 합니다: {pool}")
        net = _BACKBONES[backbone](weights=None)
        self.feat_dim = net.fc.in_features
        net.fc = nn.Identity()
        self.features = net
        self.attn = GatedAttentionPool(self.feat_dim) if pool == "attn" else None
        self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(self.feat_dim, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        f = self.features(x)
        f = self.attn(f) if self.attn is not None else f.max(dim=0, keepdim=True).values
        return self.head(f)
