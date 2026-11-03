"""
뇌 MRI 5질환 세그멘테이션 클래스 모음 (YOLO26s-Seg).

뇌 CT(class/brain)와 섞지 않는다. 8-bit PNG/JPEG MRI 2D slice 전용 prototype 이다.
질환마다 다른 sequence(T1/T1CE/T1C/DWI/FLAIR)로 학습했으므로 범용 MRI 모델이 아니다.
패키지 폴더 이름이 class 라서 아래처럼 불러온다.

    import importlib
    brain_mri = importlib.import_module("class.brain_mri")
    brain_mri.segmentation_service.predict(img_uint8)

ultralytics 는 모델을 처음 로드할 때 불러온다.
그래서 ultralytics 가 없어도 이 패키지 import 와 다른 기능은 그대로 동작한다.
"""

from .localization import Localization
from .preprocess import Preprocess
from .segmentation import SegmentationService, segmentation_service

__all__ = [
    "Localization",
    "Preprocess",
    "SegmentationService",
    "segmentation_service",
]
