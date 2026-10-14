"""
화면 공유 흉부 X-ray 분석 클래스 모음.

패키지 폴더 이름이 class 라서
`from class.thorax import ...` 문법은 쓸 수 없다.
아래처럼 불러온다.

    import importlib
    thorax = importlib.import_module("class.thorax")
    thorax.detector_service.detect(...)

소견마다 박스 탐지 모델 패키지가 하나씩 있다.
패키지 위치(THORAX_PACKAGES + THORAX_HF_REPO 또는 THORAX_LOCAL_DIR)가 정해지기 전에는
detector_service.load() 가 ThoraxModelNotReady 를 낸다.
"""

from .ensemble import (
    Detection,
    FindingModel,
    ThoraxDetector,
    ThoraxModelNotReady,
    detector_service,
)
from .localization import Localization
from .preprocess import Preprocess

__all__ = [
    "Detection",
    "FindingModel",
    "ThoraxDetector",
    "ThoraxModelNotReady",
    "detector_service",
    "Localization",
    "Preprocess",
]
