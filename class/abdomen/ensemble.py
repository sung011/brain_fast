"""
복부 CT 장기 검출 서비스 (YOLO26m).

가중치: Hugging Face ABDOMEN_HF_REPO (기본 kimsungil/abdomen_m1) 의 best.pt
또는 ABDOMEN_MODEL_PATH 로컬 파일.

AGENTS.md 의 ensemble.py 역할: 그 부위의 추론 모델.
여기서는 분류가 아니라 YOLO 박스 탐지다. 장기별 점수는 그 장기 박스의 최고 신뢰도다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import numpy as np

from . import config
from .constants import CLASSES

NOT_CONFIGURED = (
    "복부 CT 모델이 아직 준비되지 않았습니다. "
    ".env 에 ABDOMEN_HF_REPO(또는 ABDOMEN_MODEL_PATH)를 넣으세요."
)


class AbdomenModelNotReady(RuntimeError):
    """복부 모델 위치가 설정되지 않았다."""


@dataclass
class Detection:
    """박스 하나. box 는 원본 이미지 픽셀 좌표 (x1, y1, x2, y2)."""

    finding: str
    confidence: float
    box: tuple[float, float, float, float]


def _weight_path() -> Path:
    if config.MODEL_PATH:
        path = Path(config.MODEL_PATH).expanduser()
        if not path.is_file():
            raise FileNotFoundError(
                f"복부 모델 파일을 찾을 수 없습니다: {path.name} (ABDOMEN_MODEL_PATH 확인)"
            )
        return path
    repo = config.hf_repo()
    if not repo:
        raise AbdomenModelNotReady(NOT_CONFIGURED)
    from huggingface_hub import hf_hub_download

    try:
        # token=False: 공개 저장소는 토큰 없이 받는다. HF_TOKEN 을 오류 문구에 싣지 않는다(뇌 MRI 와 같음).
        downloaded = hf_hub_download(
            repo_id=repo,
            filename=config.hf_filename(),
            revision=config.hf_revision(),
            token=False,
        )
    except Exception:
        raise FileNotFoundError(
            f"복부 가중치를 받지 못했습니다. ABDOMEN_HF_REPO 를 확인하세요."
        ) from None
    return Path(downloaded)


class AbdomenDetector:
    """복부 CT YOLO 장기 검출."""

    task = "abdomen"

    def __init__(self) -> None:
        self.model = None
        self.ready = False
        self.error: str | None = None
        self._lock = Lock()
        self._names: dict[int, str] = {i: name for i, name in enumerate(CLASSES)}

    @property
    def device(self) -> str:
        if self.model is None:
            try:
                import torch

                if torch.cuda.is_available():
                    return "cuda"
                if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    return "mps"
            except ImportError:
                pass
            return "cpu"
        try:
            return str(next(self.model.model.parameters()).device)
        except Exception:
            return "unknown"

    @property
    def classes(self) -> list[str]:
        return list(CLASSES)

    def load(self) -> None:
        if self.ready:
            return
        if not config.model_configured():
            self.error = NOT_CONFIGURED
            raise AbdomenModelNotReady(NOT_CONFIGURED)
        with self._lock:
            if self.ready:
                return
            try:
                path = _weight_path()
                from ultralytics import YOLO

                model = YOLO(str(path))
                names = {int(k): str(v) for k, v in (model.names or {}).items()}
                if names:
                    self._names = names
                    expected = set(CLASSES)
                    got = set(names.values())
                    if expected != got:
                        missing = sorted(expected - got)
                        extra = sorted(got - expected)
                        raise ValueError(
                            f"가중치 클래스가 constants 와 다릅니다. "
                            f"missing={missing} extra={extra}"
                        )
                self.model = model
                self.ready = True
                self.error = None
            except Exception as exc:
                self.model = None
                self.ready = False
                self.error = str(exc)
                raise

    def use_predictor(self, predictor, names: dict[int, str] | None = None) -> None:
        """테스트용. 가짜 predictor 를 끼운다."""
        self.model = predictor
        if names is not None:
            self._names = dict(names)
        self.ready = True
        self.error = None

    def detect(self, img_uint8: np.ndarray, conf: float | None = None) -> list[Detection]:
        if not self.ready:
            raise RuntimeError(self.error or "복부 모델이 로드되지 않았습니다.")
        conf = config.CONF_THRESHOLD if conf is None else conf
        with self._lock:
            results = self.model.predict(
                img_uint8,
                imgsz=config.IMG_SIZE,
                conf=conf,
                verbose=False,
            )
        result = results[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return []
        out: list[Detection] = []
        for xyxy, score, cls in zip(
            boxes.xyxy.cpu().tolist(),
            boxes.conf.cpu().tolist(),
            boxes.cls.cpu().tolist(),
        ):
            name = self._names.get(int(cls))
            if name is None:
                continue
            out.append(
                Detection(
                    finding=name,
                    confidence=float(score),
                    box=tuple(float(v) for v in xyxy),
                )
            )
        out.sort(key=lambda d: d.confidence, reverse=True)
        return out

    def scores(self, detections: list[Detection]) -> np.ndarray:
        """CLASSES 순서의 장기별 점수(그 장기 박스의 최고 신뢰도, 없으면 0)."""
        out = np.zeros(len(CLASSES), dtype=np.float32)
        index = {name: i for i, name in enumerate(CLASSES)}
        for det in detections:
            i = index.get(det.finding)
            if i is not None:
                out[i] = max(out[i], det.confidence)
        return out


detector_service = AbdomenDetector()
