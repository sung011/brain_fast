"""
흉부 X-ray 이상 부위 위치 추정(Grad-CAM) 및 overlay 시각화.

확률이 높은 소견마다 앙상블 Grad-CAM을 계산하고,
영상 바깥(검은 여백·가장자리 글자)을 뺀 영역에서 강한 부분을 마스크로 만든다.
뇌 CT처럼 밝기로 출혈을 찾는 단계는 없다.
"""
from __future__ import annotations

import base64
import io

import numpy as np
import torch
from PIL import Image, ImageFilter

from .config import HIGH_PROB_THR
from .constants import CLASS_COLORS, class_ko
from .ensemble import ThoraxEnsemble, _infer_transforms

# Grad-CAM 에서 강한 부분으로 볼 기준 (백분위, 최소값)
CAM_PERCENTILE = 85
CAM_MIN = 0.30
# 가장자리 이 비율은 마스크에서 뺀다 (촬영 정보 글자, 마커)
BORDER_RATIO = 0.03


def _cam_target_layer(model: torch.nn.Module, name: str):
    """모델 아키텍처별 Grad-CAM 대상 레이어."""
    n = name.lower()
    if "convnext" in n:
        return model.stages[-1]
    if "efficientnet" in n:
        return model.conv_head
    if "resnet" in n:
        return model.layer4
    if "densenet" in n:
        return model.features
    last = None
    for m in model.modules():
        if isinstance(m, torch.nn.Conv2d):
            last = m
    return last


def gradcam_one(
    service: ThoraxEnsemble,
    model: torch.nn.Module,
    name: str,
    img_uint8: np.ndarray,
    target_idx: int,
    out_hw: tuple[int, int],
) -> np.ndarray:
    """단일 모델·단일 클래스 Grad-CAM 히트맵 (0~1, out_hw 크기)."""
    x = _infer_transforms(image=img_uint8)["image"].unsqueeze(0).to(service.device)
    x.requires_grad_(True)

    layer = _cam_target_layer(model, name)
    feats, grads = {}, {}
    h1 = layer.register_forward_hook(lambda _m, _i, o: feats.__setitem__("v", o))
    h2 = layer.register_full_backward_hook(lambda _m, _gi, go: grads.__setitem__("v", go[0]))
    try:
        model.zero_grad(set_to_none=True)
        logits = model(x)
        logits[0, int(target_idx)].backward()
    finally:
        h1.remove()
        h2.remove()

    f, g = feats["v"], grads["v"]
    if isinstance(f, (tuple, list)):
        f = f[0]
    if isinstance(g, (tuple, list)):
        g = g[0]
    f = f[0].detach().float().cpu().numpy()
    g = g[0].detach().float().cpu().numpy()

    w = g.mean(axis=(1, 2))
    cam = np.maximum((w[:, None, None] * f).sum(0), 0)
    cam = cam / (cam.max() + 1e-8)
    cam = np.array(
        Image.fromarray((cam * 255).astype(np.uint8)).resize(out_hw[::-1], Image.BILINEAR)
    ).astype(np.float32) / 255.0
    return cam


def ensemble_gradcam(
    service: ThoraxEnsemble,
    img_uint8: np.ndarray,
    target_idx: int,
    out_hw: tuple[int, int],
) -> np.ndarray:
    cams = []
    for model, name in zip(service.models, service.model_names):
        try:
            cams.append(gradcam_one(service, model, name, img_uint8, target_idx, out_hw))
        except Exception:
            continue
    if not cams:
        return np.zeros(out_hw, dtype=np.float32)
    return np.mean(np.stack(cams, axis=0), axis=0)


def body_mask(gray: np.ndarray) -> np.ndarray:
    """검은 여백과 가장자리 띠를 뺀 촬영 영역 마스크."""
    h, w = gray.shape
    body = gray > 15
    m = Image.fromarray(body.astype(np.uint8) * 255)
    for _ in range(3):
        m = m.filter(ImageFilter.MinFilter(3))
    body = np.array(m) > 0
    by, bx = int(h * BORDER_RATIO), int(w * BORDER_RATIO)
    body[:by, :] = False
    body[h - by:, :] = False
    body[:, :bx] = False
    body[:, w - bx:] = False
    if body.sum() < 0.1 * h * w:  # 너무 어두운 캡처는 전체를 쓴다
        body = np.ones_like(body, dtype=bool)
    return body


def cam_to_mask(cam: np.ndarray, region: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cam_in = cam * region
    if cam_in.max() > 0:
        cam_in = cam_in / cam_in.max()
    vals = cam_in[region]
    thr = float(np.percentile(vals, CAM_PERCENTILE)) if vals.size else 1.0
    thr = max(thr, CAM_MIN)
    return cam_in, region & (cam_in >= thr)


def locate_abnormality(
    service: ThoraxEnsemble,
    img_uint8: np.ndarray,
    probs: np.ndarray,
    high_thr: float = HIGH_PROB_THR,
    *,
    classes: list[str] | None = None,
) -> tuple[np.ndarray, np.ndarray, dict, list[int]]:
    """
    확률이 high_thr 이상인 소견(없으면 최상위 1개)마다 Grad-CAM 마스크를 만든다.

    Returns:
        cam_union, mask_union, per_class dict, target class indices
    """
    label_names = list(classes) if classes is not None else list(service.classes)
    h, w = img_uint8.shape[:2]
    region = body_mask(img_uint8[..., 0])

    targets = [i for i, p in enumerate(probs) if float(p) >= high_thr]
    if not targets:
        targets = [int(np.argmax(probs))]

    per_class: dict = {}
    cam_union = np.zeros((h, w), dtype=np.float32)
    mask_union = np.zeros((h, w), dtype=bool)

    for ti in targets:
        if ti >= len(label_names):
            continue
        cam = ensemble_gradcam(service, img_uint8, ti, (h, w))
        cam_in, mask = cam_to_mask(cam, region)
        mask = np.array(
            Image.fromarray((mask.astype(np.uint8) * 255))
            .filter(ImageFilter.MaxFilter(3))
            .filter(ImageFilter.MinFilter(3))
        ) > 0
        per_class[label_names[ti]] = {
            "prob": float(probs[ti]),
            "cam": cam_in,
            "mask": mask,
        }
        cam_union = np.maximum(cam_union, cam_in)
        mask_union |= mask

    if cam_union.max() > 0:
        cam_union = cam_union / cam_union.max()
    return cam_union, mask_union, per_class, targets


def build_overlay_image(img_uint8: np.ndarray, per_class: dict) -> np.ndarray:
    """원본 위에 소견별 색상 마스크를 alpha blend."""
    base = img_uint8.astype(np.float32) / 255.0
    overlay = base.copy()
    for name, info in per_class.items():
        color = CLASS_COLORS.get(name, (1.0, 0.0, 0.0))
        m = info["mask"]
        if not m.any():
            continue
        for c in range(3):
            overlay[m, c] = overlay[m, c] * 0.35 + color[c] * 0.65
    return (np.clip(overlay, 0, 1) * 255).astype(np.uint8)


def overlay_to_base64(overlay: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(overlay).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def summarize_targets(
    per_class: dict,
    targets: list[int],
    *,
    classes: list[str],
) -> str:
    """
    UI용 한 줄 위치 요약 (예: '기흉 (p=0.82, 상부-환자 우측, ~1200px)').

    흉부 PA 영상은 화면 왼쪽이 환자의 오른쪽이다. 좌우는 환자 기준으로 적는다.
    """
    bits = []
    for ti in targets:
        if ti >= len(classes):
            continue
        name = classes[ti]
        info = per_class.get(name)
        if not info or not info["mask"].any():
            continue
        mask = info["mask"]
        ys, xs = np.where(mask)
        cy, cx = float(np.mean(ys)), float(np.mean(xs))
        h, w = mask.shape
        vert = "상부" if cy < h / 3 else ("하부" if cy > 2 * h / 3 else "중부")
        side = "환자 우측" if cx < w / 3 else ("환자 좌측" if cx > 2 * w / 3 else "중앙")
        bits.append(f"{class_ko(name)} (p={info['prob']:.2f}, {vert}-{side}, ~{int(mask.sum())}px)")
    return "; ".join(bits) if bits else "강조된 이상 부위 없음"


class Localization:
    """Grad-CAM 이상 부위 추정과 overlay 생성 클래스."""

    locate_abnormality = staticmethod(locate_abnormality)
    build_overlay_image = staticmethod(build_overlay_image)
    overlay_to_base64 = staticmethod(overlay_to_base64)
    summarize_targets = staticmethod(summarize_targets)
    ensemble_gradcam = staticmethod(ensemble_gradcam)
