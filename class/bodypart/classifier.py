"""
부위 자동 판별 모델 틀.

사진 한 장을 받아 "뇌 CT / 뇌 MRI / 흉부 X-ray / 무릎 MRI / 알 수 없음" 중 하나와 확신도를 돌려준다.
모델 파일(BODYPART_WEIGHTS_PATH)이 정해지기 전에는 load() 가 BodyPartNotReady 를 내고,
predict() 는 항상 ("unknown", 0.0) 을 돌려준다. 실제 모델은 뇌·흉부·무릎 사진을 모은 뒤
이 파일의 _predict_with_model() 만 채우면 된다(라우터·화면은 고치지 않는다).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import config
from .constants import LABEL_ORDER, UNKNOWN


class BodyPartNotReady(RuntimeError):
    """판별 모델이 아직 준비되지 않았을 때."""


NOT_CONFIGURED = "부위 판별 모델 준비 중입니다(BODYPART_WEIGHTS_PATH 미설정)."


class BodyPartClassifier:
    def __init__(self) -> None:
        self._model = None
        self.error: str | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """모델 파일을 읽는다. 위치가 없거나 파일이 없으면 BodyPartNotReady."""
        if self._model is not None:
            return
        if not config.model_configured():
            self.error = NOT_CONFIGURED
            raise BodyPartNotReady(NOT_CONFIGURED)
        path = Path(config.WEIGHTS_PATH)
        if not path.is_file():
            self.error = f"판별 모델 파일이 없습니다: {path.name}"
            raise BodyPartNotReady(self.error)
        # 모델 형식(예: sklearn joblib, torch)은 학습 사진이 모인 뒤 정한다.
        self.error = "판별 모델 읽기는 아직 구현 전입니다."
        raise BodyPartNotReady(self.error)

    def use_model(self, model) -> None:
        """테스트나 나중에 붙일 모델을 직접 넣는다. model(img_uint8) → {라벨: 확률}"""
        self._model = model
        self.error = None

    def predict(self, img_uint8: np.ndarray) -> tuple[str, float]:
        """사진 → (라벨, 확신도 0~1). 모델이 없으면 ("unknown", 0.0)."""
        if self._model is None:
            return UNKNOWN, 0.0
        return self._predict_with_model(img_uint8)

    def _predict_with_model(self, img_uint8: np.ndarray) -> tuple[str, float]:
        probs = self._model(img_uint8)
        best = max(LABEL_ORDER, key=lambda code: float(probs.get(code, 0.0)))
        return best, float(probs.get(best, 0.0))


classifier_service = BodyPartClassifier()
