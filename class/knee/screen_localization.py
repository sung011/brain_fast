"""
'넘기며 분석' 결과의 ACL 대략적 위치 안내 — MSN-20261021-001 CAM_LOCALIZATION_STEP2_DISPLAY_DESIGN 2·3·4장.

- 점수와 위치를 분리한다: 점수는 여러 장 분류 모델(acl_volume) 그대로, 위치는 **별도 ACL 위치 모델(YOLO acl_yolo.pt)** 결과다.
  여러 장 모델의 Grad-CAM 은 ACL 넓은 영역과 위치 일치가 낮아(CAM_LOCALIZATION_STEP1_RESULT) 넘기며 분석 화면에서 쓰지 않는다.
- 표시할 장: 서버가 중복 제거를 마친 실제 사용 목록 U 의 가운데 장 j = len(U)//2 **한 장만**, YOLO 1회. CAM 반응·정답 ROI·점수로
  장을 고르지 않고, 다른 장으로 다시 찾거나 박스를 빌려 오지 않는다.
- 모델 파일: 이미 쓰던 acl_yolo.pt 를 **HF 캐시에서만** 찾고(내려받기·토큰 사용 없음) 고정 SHA 와 같을 때만 불러온다. 다르거나 없으면 위치만 unavailable.
- 유효 출력의 전·후처리는 Localization.locate_acl 과 같다(0~1 회색 → uint8, imgsz 320, conf .25, 가장 확신 높은 박스 1개, 좌표 4자리).
  원 좌표의 Inf/범위 밖 값은 보정 전에 거절한다. KNEE_SCREEN_LOCALIZATION_ENABLED가 정확히 1일 때만 위치를 표시한다.
  Localization 의 YOLO 싱글턴과는 따로 둔다(그 싱글턴의 초기화·내려받기 경로를 건드리지 않음). 이 모듈의 로드·추론은 자체 잠금으로 한 번에 하나.
- 그림: 원 캡처 영상 그대로(회전·뒤집기·자르기 없음), 종횡비 유지·긴 변 최대 512px, 박스는 **테두리만**(채움·열지도 색 없음).
- 실패해도 분류 결과를 무효화하지 않는다: 알려진 실패는 status 로 닫고 박스·그림을 내지 않는다(가짜 박스·다른 장·CAM 대체 없음).
  응답·로그에 경로·토큰·예외 원문을 넣지 않는다. 입력·그림은 요청 수명 안에서만 쓰고 저장하지 않는다.
"""
from __future__ import annotations

import base64
import hashlib
import io
import math
import os
import threading

import numpy as np

YOLO_FILE = "acl_yolo.pt"
YOLO_SHA256 = "d9699c11f153a1c7e13bd7a2a82b9661a8c7829e7cc0009ad5f2e12f53848508"
YOLO_BYTES = 6228900
CONF = 0.25
IMGSZ = 320
MAX_PNG_SIDE = 512
EDGE_TOL = 1e-4                       # locate_acl 의 4자리 반올림으로 생길 수 있는 끝점 오차만 허용
BOX_COLOR = (32, 214, 190)            # 청록 테두리(열지도 색조 아님)
EVIDENCE = "training_selection_exposed_not_capture_validated"
ENABLED_SETTING = "KNEE_SCREEN_LOCALIZATION_ENABLED"


def localization_enabled() -> bool:
    """서버가 명시한 1만 허용. 빈 환경값은 .env의 1로 대체하지 않는다."""
    from .config import get_setting

    value = os.environ.get(ENABLED_SETTING)
    if value is None:
        value = get_setting(ENABLED_SETTING, "0")
    return isinstance(value, str) and value.strip() == "1"


class LocationUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class InvalidLocationBox(Exception):
    """모델 원 좌표 오류. 좌표 원문은 예외에 싣지 않는다."""


class _Locator:
    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is not None:
            return self._model
        from huggingface_hub import try_to_load_from_cache

        from .config import hf_repo, hf_revision
        repo = hf_repo()
        if not repo:
            raise LocationUnavailable("not_configured")
        try:
            path = try_to_load_from_cache(repo, YOLO_FILE, revision=hf_revision())   # 캐시만(네트워크·토큰 없음)
        except Exception:
            raise LocationUnavailable("cache_missing") from None
        if not isinstance(path, str):
            raise LocationUnavailable("cache_missing")
        try:
            with open(path, "rb") as f:
                data = f.read(YOLO_BYTES + 1)
        except OSError:
            raise LocationUnavailable("cache_missing") from None
        if len(data) != YOLO_BYTES or hashlib.sha256(data).hexdigest() != YOLO_SHA256:
            raise LocationUnavailable("model_mismatch")
        try:
            from ultralytics import YOLO
            model = YOLO(path)
        except Exception:
            raise LocationUnavailable("load_failed") from None
        self._model = model
        return model

    def detect(self, gray) -> dict | None:
        """gray: (H,W) 회색(uint8 또는 0~1). 반환: {x_center,y_center,width,height,confidence}(0~1) 또는 None(못 찾음)."""
        g = np.asarray(gray)
        gray01 = g.astype(np.float64) / 255.0 if g.dtype.kind in "ui" else g.astype(np.float64)
        img_u8 = (np.clip(gray01, 0, 1) * 255).astype("uint8")              # locate_acl 과 같은 변환
        with self._lock:
            model = self._load()
            try:
                res = model.predict(img_u8, imgsz=IMGSZ, conf=CONF, verbose=False)[0]
            except Exception:
                raise LocationUnavailable("detect_failed") from None
            if len(res.boxes) == 0:
                return None
            i = int(res.boxes.conf.argmax())
            # 이상값을 먼저 [0,1]로 자르면 정상 박스처럼 보인다(CAM2-001).
            x, y, w, h = (float(v) for v in res.boxes.xywhn[i].tolist())
            if to_box({"x_center": x, "y_center": y, "width": w, "height": h}) is None:
                raise InvalidLocationBox()
            return {"x_center": round(x, 4), "y_center": round(y, 4), "width": round(w, 4), "height": round(h, 4),
                    "confidence": round(float(res.boxes.conf[i]), 4)}


locator = _Locator()


def to_box(det: dict) -> dict | None:
    """중심·크기(0~1) → 왼쪽 위 기준 {x,y,width,height}. 유한·양수·[0,1] 안(±EDGE_TOL 만 끝점 보정)이 아니면 None."""
    try:
        xc, yc, w, h = (float(det[k]) for k in ("x_center", "y_center", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (xc, yc, w, h)) or w <= 0 or h <= 0:
        return None
    x0, y0, x1, y1 = xc - w / 2, yc - h / 2, xc + w / 2, yc + h / 2
    if x0 < -EDGE_TOL or y0 < -EDGE_TOL or x1 > 1 + EDGE_TOL or y1 > 1 + EDGE_TOL:
        return None
    x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return {"x": round(x0, 6), "y": round(y0, 6), "width": round(x1 - x0, 6), "height": round(y1 - y0, 6)}


def render_png(gray, box: dict) -> tuple[str, int, int]:
    """원 캡처 회색 영상(종횡비 유지·긴 변 ≤512) 위에 박스 테두리만. 반환 (base64 PNG, 그림 너비, 높이)."""
    from PIL import Image, ImageDraw

    g = np.asarray(gray)
    u8 = g.astype("uint8") if g.dtype.kind in "ui" else (np.clip(g, 0, 1) * 255).astype("uint8")
    h, w = u8.shape
    s = min(1.0, MAX_PNG_SIDE / max(w, h))
    ow, oh = max(1, round(w * s)), max(1, round(h * s))
    im = Image.fromarray(u8).convert("RGB")
    if (ow, oh) != (w, h):
        im = im.resize((ow, oh), Image.BILINEAR)
    lw = max(2, round(max(ow, oh) / 200))
    x0, y0 = box["x"] * ow, box["y"] * oh
    x1, y1 = (box["x"] + box["width"]) * ow, (box["y"] + box["height"]) * oh
    ImageDraw.Draw(im).rectangle([round(x0), round(y0), round(x1) - 1, round(y1) - 1], outline=BOX_COLOR, width=lw)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii"), ow, oh


def locate_middle(unique: list, scope: str, include_overlay: bool) -> dict:
    """unique: 서버가 중복 제거를 마친 [(sequence, gray)]. 반환 localization dict(그림은 내부 키 '_png' 로, 응답 조립 때 꺼낸다)."""
    if not include_overlay:
        return {"status": "not_requested"}
    if scope != "full_slice":
        return {"status": "unsupported_scope", "box": None}
    if not localization_enabled():
        return {"status": "disabled_by_policy", "box": None}
    j = len(unique) // 2
    seq, gray = unique[j]
    g = np.asarray(gray)
    loc = {"status": None, "frame_selection": "middle_used_frame", "frame_ordinal": j + 1, "frame_sequence": seq,
           "frame_width": int(g.shape[1]), "frame_height": int(g.shape[0]), "coordinate_space": "captured_frame_normalized",
           "box": None, "model_id": "acl_yolo", "model_sha256": YOLO_SHA256, "evidence_status": EVIDENCE}
    try:
        det = locator.detect(gray)
    except InvalidLocationBox:
        return {**loc, "status": "invalid_box"}
    except LocationUnavailable as exc:
        return {**loc, "status": "unavailable", "reason": exc.reason}
    except Exception:
        return {**loc, "status": "unavailable", "reason": "detect_failed"}
    if det is None:
        return {**loc, "status": "not_found"}
    box = to_box(det)
    if box is None:
        return {**loc, "status": "invalid_box"}
    try:
        png, pw, ph = render_png(gray, box)
    except Exception:
        return {**loc, "status": "unavailable", "reason": "render_failed"}
    return {**loc, "status": "shown", "box": box, "image_width": pw, "image_height": ph, "_png": png}
