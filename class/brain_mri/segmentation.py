"""
뇌 MRI 5질환 YOLO26s-Seg 세그멘테이션 서비스.

- 가중치는 BRAIN_MRI_MODEL_PATH 파일이 있으면 그 파일을 읽고, 없으면 BRAIN_MRI_HF_REPO 에서 받는다.
  받은 파일은 저장소에 커밋하지 않는다.
- 첫 요청 때 로드한다. 실패하면 error 에 남기고, MRI 분석 요청만 503 이 된다.
- 결과 좌표(bbox, polygon)는 모두 입력 이미지 픽셀 기준이다.
"""
from __future__ import annotations

from pathlib import Path
from threading import Lock

import numpy as np

from . import config
from .config import CONF_THRESHOLD, MODEL_PATH
from .constants import CLASSES, class_ko


class SegmentationService:
    """ultralytics YOLO 세그멘테이션 모델을 감싼 서비스."""

    def __init__(self, model_path: str = MODEL_PATH) -> None:
        self.model_path = model_path
        self.model = None
        self.ready = False
        self.error: str | None = None
        self._lock = Lock()

    @property
    def device(self) -> str | None:
        if self.model is None:
            return None
        try:
            return str(next(self.model.model.parameters()).device)
        except Exception:
            return "unknown"

    def load(self) -> None:
        if self.ready:
            return
        with self._lock:
            if self.ready:
                return
            try:
                path = _weight_path(self.model_path)
                from ultralytics import YOLO  # 지연 import: 없거나 깨져도 다른 기능 유지

                model = YOLO(str(path))
                names = [str(model.names[i]) for i in sorted(model.names)]
                if names != CLASSES:
                    raise ValueError(
                        f"가중치 클래스 순서가 다릅니다. expected={CLASSES} got={names}"
                    )
                self.model = model
                self.ready = True
                self.error = None
            except Exception as exc:
                self.model = None
                self.ready = False
                self.error = str(exc)
                raise

    def predict(self, img_uint8: np.ndarray, conf: float | None = None) -> list[dict]:
        """HxWx3 uint8 한 장 추론. 검출이 없으면 빈 리스트."""
        self.load()
        conf = CONF_THRESHOLD if conf is None else conf
        with self._lock:  # ultralytics 모델은 스레드 안전하지 않음
            # numpy 입력은 BGR 로 해석되지만 그레이스케일 3채널 복제라 순서 영향 없음
            results = self.model.predict(img_uint8, conf=conf, verbose=False)

        result = results[0]
        preds: list[dict] = []
        if result.boxes is None or len(result.boxes) == 0:
            return preds

        boxes = result.boxes.xyxy.cpu().numpy()
        cls_ids = result.boxes.cls.cpu().numpy().astype(int)
        confs = result.boxes.conf.cpu().numpy()
        polygons = result.masks.xy if result.masks is not None else [None] * len(cls_ids)

        for box, cid, score, poly in zip(boxes, cls_ids, confs, polygons):
            name = CLASSES[cid] if 0 <= cid < len(CLASSES) else str(cid)
            preds.append(
                {
                    "class_id": int(cid),
                    "disease": name,
                    "disease_ko": class_ko(name),
                    "confidence": round(float(score), 4),
                    "bbox": [round(float(v), 1) for v in box],
                    "polygon": _round_polygon(poly),
                }
            )
        preds.sort(key=lambda p: p["confidence"], reverse=True)
        return preds


def _weight_path(model_path: str) -> Path:
    """로컬 가중치 파일. 경로가 비어 있으면 Hugging Face 저장소의 가중치를 받는다."""
    if model_path:
        path = Path(model_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(
                f"MRI 모델 파일을 찾을 수 없습니다: {path.name} (BRAIN_MRI_MODEL_PATH 확인)"
            )
        return path
    repo = config.hf_repo()
    if not repo:
        raise FileNotFoundError(
            "BRAIN_MRI_MODEL_PATH 가 비어 있습니다. .env 에 가중치 경로나 BRAIN_MRI_HF_REPO 를 지정하세요."
        )
    from huggingface_hub import hf_hub_download

    try:
        # token=False: 공개 저장소는 토큰 없이 받는다. HF_TOKEN 을 오류 문구에 싣지 않는다.
        downloaded = hf_hub_download(
            repo_id=repo, filename=config.hf_filename(), revision=config.hf_revision(), token=False
        )
    except Exception as exc:
        raise FileNotFoundError(
            f"뇌 MRI 가중치를 받지 못했습니다 ({type(exc).__name__}). BRAIN_MRI_HF_REPO 를 확인하세요."
        ) from None
    return Path(downloaded)


def _round_polygon(poly) -> list[list[float]]:
    if poly is None:
        return []
    arr = np.asarray(poly, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] < 3:
        return []
    return [[round(float(x), 1), round(float(y), 1)] for x, y in arr]


segmentation_service = SegmentationService()
