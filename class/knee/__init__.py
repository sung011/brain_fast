"""
무릎 MRI 분석 클래스 모음 (이상·ACL·반월판 분류 + ACL 위치 + Grad-CAM + 화면 캡처 한 장 분석).

패키지 폴더 이름이 class 라서
`from class.knee import ...` 문법은 쓸 수 없다.
아래처럼 불러온다.

    import importlib
    knee = importlib.import_module("class.knee")
    knee.knee_service.load()
    knee.knee_service.predict({"sagittal": volume})

무거운 라이브러리(torch 등)가 필요한 이름은 처음 쓸 때 불러온다.
그래서 `class.knee.imageio`, `class.knee.gates`, `class.knee.screen_roi` 처럼 모델을 쓰지 않는 부분은
torch 가 없는 환경(DB 없이 도는 API 계약 테스트 등)에서도 불러올 수 있다.
"""
from importlib import import_module

from .errors import KneeAnalysisError, KneeInputError, KneeModelLoadError, KneeModelNotReady

_LAZY = {
    "KneeEnsembleService": ".ensemble",
    "knee_service": ".ensemble",
    "Localization": ".localization",
    "Preprocess": ".preprocess",
}

__all__ = [
    "KneeEnsembleService",
    "knee_service",
    "KneeInputError",
    "KneeAnalysisError",
    "KneeModelNotReady",
    "KneeModelLoadError",
    "Localization",
    "Preprocess",
]


def __getattr__(name: str):
    if name in _LAZY:
        value = getattr(import_module(_LAZY[name], __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
