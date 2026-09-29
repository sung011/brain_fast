"""
ACL 위치 박스(YOLO)와 Grad-CAM 히트맵.

- 박스: "ACL 이 있는 대략적 해부학 위치" 안내일 뿐, 병변(파열) 위치가 아니다. 못 찾으면 None.
- 히트맵: 모델이 판단할 때 참고한 영역일 뿐, 병변 위치가 아니다. 반응이 없으면 강조하지 않는다.
화면에 쓸 때는 constants.DISCLAIMER 를 함께 보여준다.
"""
from __future__ import annotations

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from huggingface_hub import hf_hub_download
from PIL import Image

from .config import ACL_YOLO_FILE, IMG_SIZE, hf_repo, hf_revision
from .constants import ACL_REGION_LABEL
from .errors import KneeAnalysisError, KneeInputError
from .model import INFERENCE_LOCK
from .preprocess import Preprocess


def _check_gray(gray01: np.ndarray, name: str) -> np.ndarray:
    if not isinstance(gray01, np.ndarray) or gray01.ndim != 2 or gray01.dtype.kind not in "uif":
        raise KneeInputError(f"{name} 은 (높이, 너비) 2차원 숫자 배열이어야 합니다.")
    if gray01.size == 0 or not np.isfinite(gray01).all():
        raise KneeInputError(f"{name} 이 비어 있거나 NaN/Inf 가 들어 있습니다.")
    return gray01


class Localization:
    _yolo = None

    @classmethod
    def _get_yolo(cls):
        if cls._yolo is None:
            repo = hf_repo()
            if not repo:
                raise RuntimeError("KNEE_HF_REPO 가 비어 있습니다.")
            from ultralytics import YOLO

            path = hf_hub_download(repo_id=repo, filename=ACL_YOLO_FILE, revision=hf_revision())
            cls._yolo = YOLO(path)
        return cls._yolo

    @classmethod
    def locate_acl(cls, gray01: np.ndarray, conf: float = 0.25) -> dict | None:
        """gray01: (H,W) 0~1 슬라이스 한 장. 가장 확신 높은 박스 1개(0~1 비율 좌표) 또는 None."""
        gray01 = _check_gray(gray01, "슬라이스")
        img_u8 = (np.clip(gray01, 0, 1) * 255).astype("uint8")
        res = cls._get_yolo().predict(img_u8, imgsz=320, conf=conf, verbose=False)[0]
        if len(res.boxes) == 0:
            return None
        i = int(res.boxes.conf.argmax())
        x, y, w, h = (min(max(float(v), 0.0), 1.0) for v in res.boxes.xywhn[i].tolist())
        return {
            "label": ACL_REGION_LABEL,
            "x_center": round(x, 4),
            "y_center": round(y, 4),
            "width": round(w, 4),
            "height": round(h, 4),
            "confidence": round(float(res.boxes.conf[i]), 4),
        }

    @staticmethod
    def grad_cam(model, volume: np.ndarray, crop: float | None, device: str, n_slices: int = 4):
        """볼륨 하나에 대한 Grad-CAM. 반환: (cam (S,H,W) 0~1, 확률, 반응 큰 슬라이스 번호들).

        같은 모델로 예측과 동시에 돌지 않게 잠근다. 도중에 실패해도 hook 은 반드시 제거한다.
        """
        acts: dict = {}

        def _hook(_m, _i, out):
            out.retain_grad()
            acts["A"] = out

        with INFERENCE_LOCK:
            x = Preprocess.preprocess_volume(volume, crop=crop).to(device)
            handle = model.features.layer3.register_forward_hook(_hook)
            try:
                model.zero_grad(set_to_none=True)
                with torch.enable_grad():
                    logit = model(x)
                    prob = torch.sigmoid(logit).item()
                    logit.backward()
                a = acts["A"]
                if a.grad is None:
                    raise KneeAnalysisError("Grad-CAM 기울기를 얻지 못했습니다.")
                cam = F.relu((a.grad.mean(dim=(2, 3), keepdim=True) * a).sum(1))
            finally:
                handle.remove()
                acts.clear()

        cam = F.interpolate(cam.unsqueeze(1), size=IMG_SIZE, mode="bilinear", align_corners=False)
        cam = cam.squeeze(1).detach().cpu()
        cam = cam / (cam.amax(dim=(1, 2), keepdim=True) + 1e-6)
        if not np.isfinite(prob) or not torch.isfinite(cam).all():
            raise KneeAnalysisError("Grad-CAM 결과에 NaN/Inf 가 있습니다.")
        strength = cam.reshape(cam.shape[0], -1).sum(1)
        top = sorted(torch.argsort(strength, descending=True)[:n_slices].tolist())
        return cam.numpy(), prob, top

    @staticmethod
    def build_overlay(
        gray01: np.ndarray,
        cam01: np.ndarray,
        keep_frac: float = 0.15,
        color: tuple[int, int, int] = (255, 90, 60),
        alpha: float = 0.45,
        blur_ksize: int = 9,
        min_activation: float = 0.05,
    ) -> tuple[Image.Image, bool]:
        """Grad-CAM 상위 keep_frac 영역만 한 덩어리로 옅게 칠하고 윤곽선을 그린다. 검은 배경은 제외.

        칠할 후보는 "최댓값의 min_activation(기본 5%)보다 큰 반응"으로 제한한다. 반응이 좁은 CAM 은
        상위 15% 경계값이 0 이 되는데, 이때 0 인 영역(반응 없음)까지 칠하지 않기 위해서다.

        반환: (이미지, 강조했는지). CAM 이 전부 0 이거나 값이 일정하거나, 몸 영역 안에 유효한 반응이
        없으면 강조하지 않고 원본 그대로 돌려주며 강조 여부는 False 다.
        """
        gray01 = _check_gray(gray01, "슬라이스")
        cam01 = _check_gray(cam01, "CAM")
        if gray01.shape != cam01.shape:
            raise KneeInputError(f"슬라이스와 CAM 크기가 다릅니다: {gray01.shape} vs {cam01.shape}")

        base = np.stack([np.clip(gray01, 0, 1) * 255] * 3, axis=-1).astype("uint8")
        no_activation = (Image.fromarray(base), False)

        cam = cv2.GaussianBlur(cam01.astype("float32"), (blur_ksize, blur_ksize), 0)
        if float(cam.max() - cam.min()) < 1e-6:
            return no_activation
        cam = cam / (cam.max() + 1e-6)

        gray_u8 = (np.clip(gray01, 0, 1) * 255).astype("uint8")
        _, body = cv2.threshold(gray_u8, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        body = cv2.morphologyEx(body, cv2.MORPH_CLOSE, np.ones((9, 9), "uint8"))
        n, lab, stats, _ = cv2.connectedComponentsWithStats(body, connectivity=8)
        if n <= 1:
            return no_activation
        body = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype("uint8")

        threshold = float(np.quantile(cam, 1 - keep_frac))
        mask = ((cam > min_activation) & (cam >= threshold) & (body > 0)).astype("uint8")
        if not mask.any():
            return no_activation
        n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n > 1:
            mask = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype("uint8")

        out = base.copy()
        region = mask.astype(bool)
        out[region] = (base[region].astype("float32") * (1 - alpha) + np.array(color, "float32") * alpha).astype("uint8")
        contours, _ = cv2.findContours(mask * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(out, contours, -1, color, thickness=2)
        return Image.fromarray(out), True
