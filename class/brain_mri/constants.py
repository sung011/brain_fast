"""
뇌 MRI 5질환 도메인 상수.

- CLASSES: YOLO26s-Seg 학습 때 클래스 순서 (class_id = 인덱스). 순서를 바꾸지 않는다.
- CLASS_KO / CLASS_COLORS: UI·overlay용
"""

CLASSES = [
    "vestibular_schwannoma",
    "glioma",
    "brain_metastasis",
    "ischemic_stroke",
    "multiple_sclerosis",
]

CLASS_KO = {
    "vestibular_schwannoma": "전정신경초종",
    "glioma": "신경교종",
    "brain_metastasis": "뇌전이암",
    "ischemic_stroke": "허혈성 뇌졸중",
    "multiple_sclerosis": "다발성 경화증",
}

# overlay 색 (R, G, B) 0~1
CLASS_COLORS = {
    "vestibular_schwannoma": (0.0, 0.85, 1.0),
    "glioma": (1.0, 0.2, 0.2),
    "brain_metastasis": (1.0, 0.85, 0.1),
    "ischemic_stroke": (0.2, 1.0, 0.3),
    "multiple_sclerosis": (1.0, 0.3, 0.9),
}

CONFIDENCE_NOTE = "confidence 는 모델 confidence score 이며 질병 확률이 아닙니다."

DISCLAIMER = (
    "본 결과는 교육·연구용 AI 모델의 참고 결과이며 실제 의료 진단이 아닙니다. "
    "최종 판단은 의사에게 있습니다."
)


def class_ko(name: str) -> str:
    """영문 클래스명 → 한글명. 없으면 원문 반환."""
    return CLASS_KO.get(name, name)
