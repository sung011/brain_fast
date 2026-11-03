"""
흉부 X-ray 소견 탐지 서비스.

소견마다 박스 탐지 모델 패키지가 하나씩 있다(예: 폐음영, 흉막삼출).
모든 패키지를 차례로 돌려 박스를 모으고, 소견별 점수는 그 소견 박스의 최고 신뢰도로 한다.

패키지 폴더:
    best.pt                 가중치 (Ultralytics YOLO)
    inference_config.json   class_names, image_size, confidence_threshold,
                            nms_iou_threshold, max_detections,
                            minimum_area_ratio(선택: 이미지 면적 대비 이보다 작은 박스는 버린다)
소견 이름은 모델 파일 안의 이름이 아니라 inference_config.json 의 class_names 를 쓴다.
(흉막삼출 모델은 파일 안에 'item' 으로 저장되어 있다.)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config
from .constants import CLASSES, finding_code

NOT_CONFIGURED = (
    "흉부 X-ray 모델이 아직 준비되지 않았습니다. "
    ".env 에 THORAX_PACKAGES 와 THORAX_HF_REPO(또는 개발용 THORAX_LOCAL_DIR)를 넣으세요."
)


class ThoraxModelNotReady(RuntimeError):
    """흉부 모델 패키지 위치가 설정되지 않았다."""


@dataclass
class Detection:
    """박스 하나. box 는 원본 이미지 픽셀 좌표 (x1, y1, x2, y2)."""

    finding: str
    confidence: float
    box: tuple[float, float, float, float]


class FindingModel:
    """패키지 하나(= 소견 하나 이상)를 감싼 탐지 모델."""

    def __init__(self, name: str, settings: dict, predictor) -> None:
        self.name = name
        self.predictor = predictor
        names = settings.get("class_names") or {}
        # 모델 출력 번호 → 소견 코드
        self.findings = {int(k): finding_code(v) for k, v in names.items()}
        self.image_size = int(settings.get("image_size") or settings.get("input_image_size") or 640)
        self.confidence = float(settings.get("confidence_threshold", 0.25))
        self.nms_iou = float(settings.get("nms_iou_threshold", 0.7))
        self.max_det = int(settings.get("max_detections", 100))
        # 학습 노트북의 후처리 규칙과 같은 이름. 없으면 0 (거르지 않음)
        self.min_area_ratio = float(settings.get("minimum_area_ratio") or 0.0)

    def detect(self, img_uint8: np.ndarray) -> list[Detection]:
        result = self.predictor.predict(
            img_uint8,
            imgsz=self.image_size,
            conf=self.confidence,
            iou=self.nms_iou,
            max_det=self.max_det,
            verbose=False,
        )[0]
        out: list[Detection] = []
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return out
        image_area = float(img_uint8.shape[0] * img_uint8.shape[1]) or 1.0
        for xyxy, conf, cls in zip(
            boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(), boxes.cls.cpu().tolist()
        ):
            code = self.findings.get(int(cls))
            if code is None:
                continue
            x1, y1, x2, y2 = xyxy
            if (x2 - x1) * (y2 - y1) / image_area < self.min_area_ratio:
                continue
            out.append(Detection(code, float(conf), tuple(float(v) for v in xyxy)))
        return out


def _load_yolo(weights: Path):
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("흉부 모델 실행에 ultralytics 가 필요합니다. pip install ultralytics") from exc
    return YOLO(str(weights))


class ThoraxDetector:
    """흉부 소견 탐지 모델 묶음."""

    task = "thorax"

    def __init__(self) -> None:
        self.models: list[FindingModel] = []
        self.ready = False
        self.error: str | None = None

    @property
    def device(self) -> str:
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    @property
    def classes(self) -> list[str]:
        """불러온 소견 코드. CLASSES 순서를 따르고, 목록에 없는 소견은 뒤에 붙인다."""
        found: list[str] = []
        for model in self.models:
            for code in model.findings.values():
                if code not in found:
                    found.append(code)
        ordered = [c for c in CLASSES if c in found]
        return ordered + [c for c in found if c not in ordered]

    def _package_dir(self, name: str) -> Path:
        if config.LOCAL_DIR:
            base = Path(config.LOCAL_DIR) / name
            # zip 을 풀면 같은 이름 폴더가 한 번 더 생기는 경우를 받아 준다
            return base / name if (base / name / "inference_config.json").is_file() else base
        from huggingface_hub import snapshot_download

        root = snapshot_download(
            repo_id=config.HF_REPO,
            allow_patterns=[f"{name}/best.pt", f"{name}/inference_config.json"],
            token=config.HF_TOKEN or None,
        )
        return Path(root) / name

    def load(self) -> None:
        if self.ready:
            return
        if not config.model_configured():
            self.error = NOT_CONFIGURED
            raise ThoraxModelNotReady(NOT_CONFIGURED)
        try:
            models = []
            for name in config.PACKAGES:
                folder = self._package_dir(name)
                settings = json.loads((folder / "inference_config.json").read_text(encoding="utf-8"))
                weights = folder / (settings.get("weights") or settings.get("checkpoint_filename") or "best.pt")
                models.append(FindingModel(name, settings, _load_yolo(weights)))
            self.use_models(models)
        except Exception as exc:
            self.ready = False
            self.error = str(exc)
            raise

    def use_models(self, models: list[FindingModel]) -> None:
        """불러온 모델을 끼운다. 테스트에서는 가짜 predictor 로 만든 FindingModel 을 넣는다."""
        if not models:
            raise ValueError("흉부 모델 패키지가 하나도 없습니다.")
        self.models = list(models)
        self.ready = True
        self.error = None

    def detect(self, img_uint8: np.ndarray) -> list[Detection]:
        if not self.ready:
            raise RuntimeError(self.error or "흉부 모델이 로드되지 않았습니다.")
        detections: list[Detection] = []
        for model in self.models:
            detections.extend(model.detect(img_uint8))
        return detections

    def scores(self, detections: list[Detection]) -> np.ndarray:
        """classes 순서의 소견별 점수(그 소견 박스의 최고 신뢰도, 없으면 0)."""
        classes = self.classes
        out = np.zeros(len(classes), dtype=np.float32)
        for det in detections:
            if det.finding in classes:
                i = classes.index(det.finding)
                out[i] = max(out[i], det.confidence)
        return out


# 싱글톤 — thorax 라우터와 ROI 채점에서 공유
detector_service = ThoraxDetector()
