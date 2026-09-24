"""
이상 부위 위치 추정(Grad-CAM) 및 overlay 시각화.

고확률 클래스에 대해 앙상블 Grad-CAM을 계산하고,
강도 기반 출혈 마스크·뇌 내부 마스크와 결합해 colored overlay PNG를 생성한다.
"""
from __future__ import annotations

import base64
import io
from collections import deque

import numpy as np
import torch
from PIL import Image, ImageFilter

from .config import HIGH_PROB_THR
from .constants import CLASS_COLORS, CLASSES, class_ko
from .ensemble import TaskEnsemble, _infer_transforms


def _cam_target_layer(model: torch.nn.Module, name: str):
    """모델 아키텍처별 Grad-CAM 대상 conv 레이어."""
    n = name.lower()
    if "convnext" in n:
        return model.stages[-1]
    if "efficientnet" in n:
        return model.conv_head
    if "resnet" in n:
        return model.layer4
    last = None
    for m in model.modules():
        if isinstance(m, torch.nn.Conv2d):
            last = m
    return last


def gradcam_one(
    service: TaskEnsemble,
    model: torch.nn.Module,
    name: str,
    img_uint8: np.ndarray,
    target_idx: int,
    out_hw: tuple[int, int],
) -> np.ndarray:
    """단일 모델·단일 클래스에 대한 Grad-CAM 히트맵 (0~1, out_hw 크기)."""
    x = _infer_transforms(image=img_uint8)["image"].unsqueeze(0).to(service.device)
    x.requires_grad_(True)

    layer = _cam_target_layer(model, name)
    feats, grads = {}, {}

    h1 = layer.register_forward_hook(lambda _m, _i, o: feats.__setitem__("v", o))
    h2 = layer.register_full_backward_hook(lambda _m, _gi, go: grads.__setitem__("v", go[0]))

    model.zero_grad(set_to_none=True)
    logits = model(x)
    logits[0, int(target_idx)].backward()

    f = feats["v"]
    g = grads["v"]
    h1.remove()
    h2.remove()

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


def brain_interior_mask(gray: np.ndarray) -> np.ndarray:
    """두개골·골板 제외 뇌 실질 내부 영역 마스크."""
    head = gray > 12
    bone = gray >= 215
    soft = (head & ~bone).astype(np.uint8) * 255
    img_m = Image.fromarray(soft)
    for _ in range(6):
        img_m = img_m.filter(ImageFilter.MinFilter(3))
    return np.array(img_m) > 0


def intensity_hemorrhage_mask(gray: np.ndarray, interior: np.ndarray) -> np.ndarray:
    """고밀도 연결 영역 기반 출혈 후보 마스크 (connected component)."""
    if interior.sum() < 100:
        return np.zeros_like(gray, dtype=bool)
    thr = max(float(np.percentile(gray[interior], 96)), float(gray[interior].mean() + 25))
    cand = interior & (gray >= thr) & (gray < 230)
    visited = np.zeros_like(cand, dtype=bool)
    mask = np.zeros_like(cand, dtype=bool)
    h, w = cand.shape
    for y in range(h):
        for x in range(w):
            if not cand[y, x] or visited[y, x]:
                continue
            q = deque([(y, x)])
            visited[y, x] = True
            pix = []
            while q:
                cy, cx = q.popleft()
                pix.append((cy, cx))
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < h and 0 <= nx < w and cand[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        q.append((ny, nx))
            if len(pix) >= 10:
                ys, xs = zip(*pix)
                mask[ys, xs] = True
    m = Image.fromarray((mask.astype(np.uint8) * 255)).filter(ImageFilter.MaxFilter(3))
    return np.array(m) > 0


def cam_to_mask(cam: np.ndarray, interior: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cam_in = cam * interior
    if cam_in.max() > 0:
        cam_in = cam_in / cam_in.max()
    vals = cam_in[interior]
    thr = float(np.percentile(vals, 90)) if vals.size else 1.0
    thr = max(thr, 0.22)
    return cam_in, interior & (cam_in >= thr)


def ensemble_gradcam(
    service: TaskEnsemble,
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


def locate_abnormality(
    service: TaskEnsemble,
    img_uint8: np.ndarray,
    probs: np.ndarray,
    high_thr: float = HIGH_PROB_THR,
    *,
    classes: list[str] | None = None,
    task: str = "hemorrhage",
) -> tuple[np.ndarray, np.ndarray, dict, list[int]]:
    """
    분류 확률 기반 대상 클래스 선정 → Grad-CAM + 마스크 fusion.

    task=hemorrhage: 강도 기반 출혈 마스크와 결합
    task=germinoma: Grad-CAM + 뇌 내부 마스크 위주

    Returns:
        cam_union, mask_union, per_class dict, target class indices
    """
    label_names = list(classes) if classes is not None else list(getattr(service, "classes", CLASSES))
    h, w = img_uint8.shape[:2]
    gray = img_uint8[..., 0].astype(np.float32)
    interior = brain_interior_mask(gray)

    if task == "germinoma":
        return _locate_germinoma(service, img_uint8, probs, label_names, interior, high_thr)

    ich_mask = intensity_hemorrhage_mask(gray, interior)

    n = len(probs)
    subtype_n = min(5, max(0, n - 1)) if n >= 2 else n
    targets = [i for i, p in enumerate(probs) if float(p) >= high_thr]
    if not targets:
        if subtype_n > 0:
            targets = [int(np.argmax(probs[:subtype_n]))]
        else:
            targets = [int(np.argmax(probs))]
        if n >= 6 and float(probs[5]) >= 0.45:
            targets.append(5)
        targets = sorted(set(targets))

    per_class: dict = {}
    cam_union = np.zeros((h, w), dtype=np.float32)
    mask_union = np.zeros((h, w), dtype=bool)

    for ti in targets:
        if ti >= len(label_names):
            continue
        cam = ensemble_gradcam(service, img_uint8, ti, (h, w))
        cam_in, cam_mask = cam_to_mask(cam, interior)

        any_p = float(probs[5]) if n >= 6 else 0.0
        if any_p >= 0.45 or float(probs[ti]) >= high_thr:
            inter = cam_mask & ich_mask
            mask = inter if inter.sum() >= 8 else (cam_mask | ich_mask)
        else:
            mask = ich_mask if ich_mask.sum() >= 40 else cam_mask

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


def _locate_germinoma(
    service: TaskEnsemble,
    img_uint8: np.ndarray,
    probs: np.ndarray,
    label_names: list[str],
    interior: np.ndarray,
    high_thr: float,
) -> tuple[np.ndarray, np.ndarray, dict, list[int]]:
    """germinoma(tumor) Grad-CAM 위치 추정 — 출혈 강도 마스크는 쓰지 않음."""
    h, w = img_uint8.shape[:2]
    targets = [i for i, p in enumerate(probs) if float(p) >= high_thr]
    if not targets:
        targets = [int(np.argmax(probs))]

    per_class: dict = {}
    cam_union = np.zeros((h, w), dtype=np.float32)
    mask_union = np.zeros((h, w), dtype=bool)

    for ti in targets:
        if ti >= len(label_names):
            continue
        if float(probs[ti]) < 0.35:
            continue
        cam = ensemble_gradcam(service, img_uint8, ti, (h, w))
        cam_in, cam_mask = cam_to_mask(cam, interior)
        mask = cam_mask if cam_mask.any() else (interior & (cam_in >= 0.35))
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
    """원본 위에 클래스별 색상 마스크를 alpha blend."""
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
    classes: list[str] | None = None,
) -> str:
    """UI·프롬프트용 한 줄 위치 요약 (예: '경막하출혈 (p=0.92, 중부-좌측)')."""
    label_names = list(classes) if classes is not None else list(CLASSES)
    bits = []
    for ti in targets:
        if ti >= len(label_names):
            continue
        name = label_names[ti]
        info = per_class.get(name)
        if not info or not info["mask"].any():
            continue
        mask = info["mask"]
        ys, xs = np.where(mask)
        if ys.size == 0:
            continue
        cy, cx = float(np.mean(ys)), float(np.mean(xs))
        h, w = mask.shape
        vert = "상부" if cy < h / 3 else ("하부" if cy > 2 * h / 3 else "중부")
        horiz = "좌측" if cx < w / 3 else ("우측" if cx > 2 * w / 3 else "중앙")
        px = int(mask.sum())
        bits.append(f"{class_ko(name)} (p={info['prob']:.2f}, {vert}-{horiz}, ~{px}px)")
    return "; ".join(bits) if bits else "강조된 이상 부위 없음"


class Localization:
    """Grad-CAM 이상 부위 추정과 overlay 생성 클래스."""

    locate_abnormality = staticmethod(locate_abnormality)
    build_overlay_image = staticmethod(build_overlay_image)
    overlay_to_base64 = staticmethod(overlay_to_base64)
    summarize_targets = staticmethod(summarize_targets)
    ensemble_gradcam = staticmethod(ensemble_gradcam)
