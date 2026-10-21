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

# 질환마다 학습에 쓴 MRI sequence (공개 데이터셋 기준). 범용 MRI 모델이 아니다.
TRAINED_SEQUENCES = {
    "vestibular_schwannoma": "T1",
    "glioma": "T1CE",
    "brain_metastasis": "T1C",
    "ischemic_stroke": "DWI",
    "multiple_sclerosis": "FLAIR",
}

# 요청의 sequence 값. 추론 방식은 바꾸지 않고 응답에 그대로 기록만 한다.
SEQUENCES = ["unknown", "T1", "T1CE", "T1C", "DWI", "FLAIR", "other"]

INPUT_SCOPE = (
    "지원 전처리·sequence 가 확인된 8-bit PNG/JPEG 2D MRI slice 에 대한 prototype inference 입니다."
)

LIMITATIONS = [
    "질환마다 서로 다른 sequence 로 학습했습니다: "
    "전정신경초종 T1, 신경교종 T1CE, 뇌전이암 T1C, 허혈성 뇌졸중 DWI, 다발성 경화증 FLAIR.",
    "임의의 MRI 에서 5질환을 판별하는 범용 모델이 아닙니다. "
    "학습과 다른 sequence·기관·전처리의 영상은 결과를 신뢰할 수 없습니다.",
    "DICOM 직접 추론은 지원하지 않습니다. 학습 PNG 생성 방식(볼륨/slice 정규화, 축 방향)이 질환마다 달라 재현할 수 없습니다.",
    "검출 0건은 병변이 없다는 뜻이 아닙니다.",
]

CONFIDENCE_NOTE = "confidence 는 모델 confidence score 이며 질병 확률이 아닙니다."

DISCLAIMER = (
    "본 결과는 교육·연구용 AI 모델의 참고 결과이며 실제 의료 진단이 아닙니다. "
    "최종 판단은 의사에게 있습니다."
)


def class_ko(name: str) -> str:
    """영문 클래스명 → 한글명. 없으면 원문 반환."""
    return CLASS_KO.get(name, name)
