"""
복부 CT 장기 검출 도메인 상수.

- CLASSES: YOLO26m(abdomen_m1) 출력 클래스 순서 (학습 README 와 동일)
- CLASS_KO / CLASS_COLORS: UI·판독문·overlay 용
"""

CLASSES = [
    "adrenal_gland",
    "bladder",
    "colon",
    "duodenum",
    "esophagus",
    "gallbladder",
    "kidney",
    "liver",
    "pancreatic",
    "prostate",
    "spleen",
    "stomach",
    "uterus",
]

CLASS_KO = {
    "adrenal_gland": "부신",
    "bladder": "방광",
    "colon": "대장",
    "duodenum": "십이지장",
    "esophagus": "식도",
    "gallbladder": "담낭",
    "kidney": "신장",
    "liver": "간",
    "pancreatic": "췌장",
    "prostate": "전립선",
    "spleen": "비장",
    "stomach": "위",
    "uterus": "자궁",
}

# overlay 색 (0~1 RGB). 장기마다 구분되게 둔다.
CLASS_COLORS = {
    "adrenal_gland": (0.95, 0.55, 0.20),
    "bladder": (0.20, 0.75, 0.95),
    "colon": (0.45, 0.85, 0.35),
    "duodenum": (0.85, 0.70, 0.25),
    "esophagus": (0.70, 0.45, 0.90),
    "gallbladder": (0.15, 0.90, 0.70),
    "kidney": (0.95, 0.35, 0.45),
    "liver": (0.90, 0.25, 0.25),
    "pancreatic": (1.00, 0.75, 0.20),
    "prostate": (0.55, 0.40, 0.95),
    "spleen": (0.85, 0.20, 0.75),
    "stomach": (0.30, 0.55, 1.00),
    "uterus": (1.00, 0.45, 0.70),
    # ROI 채점 결과의 의심 부위(장기 구분 없이 한 색). 흉부 채점 범례와 맞춘다
    "answer": (0.08, 0.72, 0.65),
    # 데이터셋 정답이 기준일 때 AI 가 찾은 부위는 참고로만 파란 테두리로 그린다
    "ai_ref": (0.25, 0.45, 1.0),
}

DISCLAIMER = (
    "본 결과는 AI 보조 참고용이며 실제 의료 진단이 아닙니다. "
    "복부 CT 장기 위치 학습용이며, 최종 판단은 의사에게 있습니다."
)


def class_ko(name: str) -> str:
    """영문 클래스명 → 한글명. 없으면 원문 반환."""
    return CLASS_KO.get(name, name)
