"""
흉부 X-ray 소견 상수.

- CLASSES: 소견 코드의 표시 순서. 모델은 소견마다 따로 있고(패키지 하나 = 소견 하나),
  실제로 불러온 소견만 결과에 나온다.
- LABELS: 소견 코드별 표시 이름. 언어 코드(ko, en)별로 둔다. 다국어는 여기에 언어를 추가한다.
- ALIASES: 모델 설정 파일의 이름(예: "Pleural effusion", "airspace_opacity")을 소견 코드로 바꾼다.
- CLASS_COLORS: overlay 색 (0~1 RGB)
"""
import re

CLASSES = [
    "airspace_opacity",
    "pleural_effusion",
    "pneumothorax",
    "cardiomegaly",
    "consolidation",
]

DEFAULT_LANG = "ko"

LABELS = {
    "airspace_opacity": {"ko": "폐음영", "en": "Airspace opacity"},
    "pleural_effusion": {"ko": "흉막삼출", "en": "Pleural effusion"},
    "pneumothorax": {"ko": "기흉", "en": "Pneumothorax"},
    "cardiomegaly": {"ko": "심비대", "en": "Cardiomegaly"},
    "consolidation": {"ko": "폐경화", "en": "Consolidation"},
}

# 정규화한 이름(소문자, 공백·하이픈 → _) → 소견 코드
ALIASES = {
    "lung_opacity": "airspace_opacity",
    "opacity": "airspace_opacity",
    "effusion": "pleural_effusion",
    "ptx": "pneumothorax",
}

CLASS_COLORS = {
    "airspace_opacity": (0.2, 1.0, 0.3),
    "pleural_effusion": (0.1, 0.6, 1.0),
    "pneumothorax": (1.0, 0.75, 0.3),
    "cardiomegaly": (1.0, 0.2, 0.9),
    "consolidation": (1.0, 0.15, 0.15),
    # ROI 채점 결과의 의심 부위(소견 구분 없이 한 색). 학습자 화면 범례(#14b8a6)와 맞춘다
    "answer": (0.08, 0.72, 0.65),
    # 데이터셋 정답이 기준일 때 AI 가 찾은 부위는 참고로만 파란 테두리로 그린다
    "ai_ref": (0.25, 0.45, 1.0),
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


def finding_code(name: str) -> str:
    """모델이 쓰는 이름을 소견 코드로 바꾼다. 예: 'Pleural effusion' → 'pleural_effusion'"""
    key = re.sub(r"[\s\-/]+", "_", str(name).strip().lower())
    return ALIASES.get(key, key)


def class_label(code: str, lang: str = DEFAULT_LANG) -> str:
    """소견 코드 → 표시 이름. 그 언어가 없으면 한국어, 그것도 없으면 코드 그대로."""
    names = LABELS.get(code) or {}
    return names.get(lang) or names.get(DEFAULT_LANG) or code


def class_ko(code: str) -> str:
    return class_label(code, "ko")


def disclaimer(lang: str = DEFAULT_LANG) -> str:
    return DISCLAIMERS.get(lang) or DISCLAIMER
