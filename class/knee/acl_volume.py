"""
'넘기며 분석'(화면 공유로 모은 여러 장) 전용 ACL 시상면 볼륨 모델 하나 — MSN-20261021-001 v2 설계 7장.

- best_acl_combined.pt(ACL × sagittal) 체크포인트 **하나만** 처음 쓸 때 불러온다(lazy). 다른 소견·촬영면·시드 모델, YOLO, 뇌 모델은 불러오지 않는다.
  (기존 knee_service.ensure_ready 는 모든 무릎 모델을 한꺼번에 올리므로 이 경로에서 쓰지 않는다 — 메모리 부족 기록 반영)
- 같은 여러 장 입력을 모델에 한 번에 넣어(특징 max-pool) 점수를 낸다. 장별 점수 평균으로 바꾸지 않는다.
- 히트맵은 **같은 입력의 같은 logit** 에 대한 Grad-CAM 이다(Localization.grad_cam). 반응이 가장 큰 캡처 한 장과 그 순번을 돌려준다.
- 기본 CPU(KNEE_SCROLL_DEVICE=cuda 이고 GPU 가 있을 때만 GPU). 추론은 model.INFERENCE_LOCK 으로 다른 무릎 추론과 겹치지 않게 한다.
"""
from __future__ import annotations

import hashlib
import threading

import numpy as np

from .errors import KneeAnalysisError, KneeModelNotReady

TASK, PLANE = "acl", "sagittal"


class AclVolumeModel:
    def __init__(self) -> None:
        self.model = None
        self.crop = None
        self.device = "cpu"
        self.model_id = None
        self.model_sha256 = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.model is not None

    def load(self) -> None:
        with self._lock:
            if self.model is not None:
                return
            try:
                import torch

                from .config import CKPT_FILES, get_setting
                from .ensemble import KneeEnsembleService
                from .model import MRNetModel

                files = CKPT_FILES[(TASK, PLANE)]
                if len(files) != 1:
                    raise KneeAnalysisError("ACL 시상면 체크포인트가 하나가 아닙니다.")
                path = KneeEnsembleService._download(files[0])   # HF 캐시가 있으면 그것을 쓴다(토큰 처리·오류 가림은 기존 함수 그대로)
                device = "cuda" if get_setting("KNEE_SCROLL_DEVICE", "cpu") == "cuda" and torch.cuda.is_available() else "cpu"
                ck = torch.load(path, map_location=device, weights_only=True)
                m = MRNetModel(backbone=ck.get("backbone", "resnet18"), pool=ck.get("pool", "max"))
                m.load_state_dict(ck["model"])
                m.to(device).eval()
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
            except Exception:
                raise KneeModelNotReady("ACL 분석 모델을 준비하지 못했습니다.") from None
            self.model, self.crop, self.device = m, ck.get("crop"), device
            self.model_id, self.model_sha256 = f"{files[0]}@{sha[:12]}", sha

    def analyze(self, grays: list[np.ndarray], *, include_overlay: bool = True) -> dict:
        """grays: 같은 크기 (H,W) uint8 여러 장(입력 순서 = 캡처 순서). 반환: raw 점수·CAM 대상 장 index·오버레이(PIL 또는 None)."""
        import torch

        from .localization import Localization
        from .model import INFERENCE_LOCK
        from .preprocess import Preprocess

        self.load()
        volume = Preprocess.validate_volume(np.stack([np.ascontiguousarray(g) for g in grays]))
        if not include_overlay:
            with INFERENCE_LOCK, torch.no_grad():
                x = Preprocess.preprocess_volume(volume, crop=self.crop).to(self.device)
                raw = float(torch.sigmoid(self.model(x)).item())
            return {"raw_score": _finite(raw), "cam_index": None, "overlay": None, "activated": False}
        cam, prob, top = Localization.grad_cam(self.model, volume, self.crop, self.device, n_slices=1)
        idx = int(top[0])
        shown = Preprocess.to_gray01(Preprocess.preprocess_volume(volume, crop=self.crop)[idx])   # 모델이 본 것과 같은 볼륨 정규화
        image, activated = Localization.build_overlay(shown, cam[idx])
        return {"raw_score": _finite(prob), "cam_index": idx, "overlay": image, "activated": bool(activated)}


def _finite(p: float) -> float:
    if not (isinstance(p, float) and np.isfinite(p) and 0.0 <= p <= 1.0):
        raise KneeAnalysisError("모델 점수가 올바르지 않습니다.")
    return p


acl_volume = AclVolumeModel()
