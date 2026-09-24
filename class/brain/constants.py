"""
뇌 CT 분류 도메인 상수.

- CLASSES: 출혈(ICH) 모델 출력 순서 (6개)
- GERMINOMA_CLASSES: germinoma CT 이진 분류 (tumor 1개)
- CLASS_KO / CLASS_COLORS: UI·판독문·overlay용
"""

CLASSES = [
    "epidural",
    "intraparenchymal",
    "intraventricular",
    "subarachnoid",
    "subdural",
    "any",
]

GERMINOMA_CLASSES = [
    "tumor",
]

CLASS_KO = {
    "epidural": "경막외출혈",
    "intraparenchymal": "뇌실질내출혈",
    "intraventricular": "뇌실내출혈",
    "subarachnoid": "지주막하출혈",
    "subdural": "경막하출혈",
    "any": "두개내출혈",
    "tumor": "생식세포종양",
}

CLASS_COLORS = {
    "epidural": (0.0, 0.85, 1.0),
    "intraparenchymal": (1.0, 0.15, 0.15),
    "intraventricular": (1.0, 0.9, 0.1),
    "subarachnoid": (0.2, 1.0, 0.3),
    "subdural": (1.0, 0.2, 0.9),
    "any": (1.0, 0.55, 0.0),
    "tumor": (0.55, 0.35, 1.0),
}

DISCLAIMER = (
    "본 결과는 AI 보조 참고용이며 실제 의료 진단이 아닙니다. "
    "최종 판단은 의사에게 있습니다."
)


def class_ko(name: str) -> str:
    """영문 클래스명 → 한글명. 없으면 원문 반환."""
    return CLASS_KO.get(name, name)
