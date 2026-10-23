"""
화면 공유 사진의 부위(뇌 CT·뇌 MRI·흉부 X-ray·무릎 MRI)를 맞히는 클래스 모음.

패키지 폴더 이름이 class 라서
`from class.bodypart import ...` 문법은 쓸 수 없다.
아래처럼 불러온다.

    import importlib
    bodypart = importlib.import_module("class.bodypart")
    label, confidence = bodypart.classifier_service.predict(img_uint8)

모델이 준비되기 전에는 항상 "알 수 없음"을 돌려준다(경고를 내지 않는다).
"""

from .classifier import BodyPartClassifier, BodyPartNotReady, classifier_service
from .constants import DROPDOWN_TO_LABEL, LABELS, UNKNOWN, label_ko

__all__ = [
    "BodyPartClassifier",
    "BodyPartNotReady",
    "classifier_service",
    "DROPDOWN_TO_LABEL",
    "LABELS",
    "UNKNOWN",
    "label_ko",
]
