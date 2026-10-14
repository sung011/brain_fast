"""
흉부 X-ray 분류 도메인 상수.

- CLASSES: 기본 클래스 순서 (VinDr-CXR 14개 소견, 잠정)
  체크포인트에 classes 목록이 있으면 로드할 때 그 순서로 바뀐다.
  학습 때 순서를 바꾸지 않는다.
- LABELS: 클래스별 표시 이름. 언어 코드(ko, en)별로 둔다. 다국어는 여기에 언어를 추가한다.
- CLASS_COLORS: overlay 색 (0~1 RGB)
"""

CLASSES = [
    "Aortic enlargement",
    "Atelectasis",
    "Calcification",
    "Cardiomegaly",
    "Consolidation",
    "ILD",
    "Infiltration",
    "Lung Opacity",
    "Nodule/Mass",
    "Other lesion",
    "Pleural effusion",
    "Pleural thickening",
    "Pneumothorax",
    "Pulmonary fibrosis",
]

DEFAULT_LANG = "ko"

# VinDr-CXR 14개 + CheXlocalize(CheXpert) 소견. 팀 모델의 클래스가 정해지면 빠진 것만 추가한다.
LABELS = {
    "Aortic enlargement": {"ko": "대동맥 확장", "en": "Aortic enlargement"},
    "Atelectasis": {"ko": "무기폐", "en": "Atelectasis"},
    "Calcification": {"ko": "석회화", "en": "Calcification"},
    "Cardiomegaly": {"ko": "심비대", "en": "Cardiomegaly"},
    "Consolidation": {"ko": "경화", "en": "Consolidation"},
    "ILD": {"ko": "간질성 폐질환", "en": "Interstitial lung disease"},
    "Infiltration": {"ko": "침윤", "en": "Infiltration"},
    "Lung Opacity": {"ko": "폐 음영", "en": "Lung opacity"},
    "Nodule/Mass": {"ko": "결절/종괴", "en": "Nodule/Mass"},
    "Other lesion": {"ko": "기타 병변", "en": "Other lesion"},
    "Pleural effusion": {"ko": "흉막삼출", "en": "Pleural effusion"},
    "Pleural thickening": {"ko": "흉막비후", "en": "Pleural thickening"},
    "Pneumothorax": {"ko": "기흉", "en": "Pneumothorax"},
    "Pulmonary fibrosis": {"ko": "폐섬유증", "en": "Pulmonary fibrosis"},
    "No finding": {"ko": "이상 소견 없음", "en": "No finding"},
    "Enlarged Cardiomediastinum": {"ko": "심종격동 확장", "en": "Enlarged cardiomediastinum"},
    "Lung Lesion": {"ko": "폐 병변", "en": "Lung lesion"},
    "Airspace Opacity": {"ko": "폐포 음영", "en": "Airspace opacity"},
    "Edema": {"ko": "폐부종", "en": "Edema"},
    "Pleural Effusion": {"ko": "흉막삼출", "en": "Pleural effusion"},
    "Support Devices": {"ko": "의료기구", "en": "Support devices"},
}

# 클래스 이름에 색이 없으면 build_overlay_image 에서 빨강을 쓴다.
CLASS_COLORS = {
    "Aortic enlargement": (1.0, 0.55, 0.0),
    "Atelectasis": (0.0, 0.85, 1.0),
    "Calcification": (0.9, 0.9, 0.9),
    "Cardiomegaly": (1.0, 0.2, 0.9),
    "Consolidation": (1.0, 0.15, 0.15),
    "ILD": (0.55, 0.35, 1.0),
    "Infiltration": (1.0, 0.9, 0.1),
    "Lung Opacity": (0.2, 1.0, 0.3),
    "Nodule/Mass": (1.0, 0.4, 0.4),
    "Other lesion": (0.6, 0.6, 1.0),
    "Pleural effusion": (0.1, 0.6, 1.0),
    "Pleural thickening": (0.4, 0.9, 0.8),
    "Pneumothorax": (1.0, 0.75, 0.3),
    "Pulmonary fibrosis": (0.75, 0.5, 0.25),
}

DISCLAIMERS = {
    "ko": (
        "본 결과는 AI 보조 참고용이며 실제 의료 진단이 아닙니다. "
        "최종 판단은 의사에게 있습니다."
    ),
    "en": (
        "This result is an AI-assisted reference for learning, not a medical diagnosis. "
        "The final judgment rests with a physician."
    ),
}
DISCLAIMER = DISCLAIMERS[DEFAULT_LANG]


def class_label(name: str, lang: str = DEFAULT_LANG) -> str:
    """클래스명 → 표시 이름. 그 언어가 없으면 한국어, 그것도 없으면 원문."""
    names = LABELS.get(name) or {}
    return names.get(lang) or names.get(DEFAULT_LANG) or name


def class_ko(name: str) -> str:
    """영문 클래스명 → 한글명. 없으면 원문 반환."""
    return class_label(name, "ko")


def disclaimer(lang: str = DEFAULT_LANG) -> str:
    return DISCLAIMERS.get(lang) or DISCLAIMER
