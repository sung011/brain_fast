"""
화면 공유 흉부 X-ray 분석 클래스 모음.

패키지 폴더 이름이 class 라서
`from class.thorax import ...` 문법은 쓸 수 없다.
아래처럼 불러온다.

    import importlib
    thorax = importlib.import_module("class.thorax")
    thorax.ensemble_service.predict(...)

모델 가중치 위치(THORAX_HF_REPO, THORAX_CKPT_FILES)가 정해지기 전에는
ensemble_service.load() 가 ThoraxModelNotReady 를 낸다.
"""

from .ensemble import ThoraxEnsemble, ThoraxModelNotReady, ensemble_service
from .localization import Localization
from .preprocess import Preprocess

__all__ = [
    "ThoraxEnsemble",
    "ThoraxModelNotReady",
    "ensemble_service",
    "Localization",
    "Preprocess",
]
