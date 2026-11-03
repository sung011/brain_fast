"""
부위 자동 판별 라벨.

화면 공유 분석 드롭다운은 "부위 + 영상 종류" 묶음(뇌 CT, 흉부 X-ray …)으로 되어 있어서
판별 라벨도 같은 단위로 둔다. 부위가 늘면 LABELS 와 DROPDOWN_TO_LABEL 에 한 줄씩 더하고
그 라벨 사진으로 판별 모델을 다시 학습한다.
"""

UNKNOWN = "unknown"

# 판별 라벨 → 한국어 이름. unknown 은 "어느 라벨에도 안 맞음/확신 낮음"이라 경고를 내지 않는다.
LABELS = {
    "brain_ct": "뇌 CT",
    "brain_mri": "뇌 MRI",
    "thorax_xray": "흉부 X-ray",
    "knee_mri": "무릎 MRI",
    UNKNOWN: "알 수 없음",
}

# 화면 공유 페이지 드롭다운 값(templates/screen_analyze.html 의 bodyPart.value) → 기대하는 라벨.
# 드롭다운에 없는 값이면 경고를 내지 않는다.
DROPDOWN_TO_LABEL = {
    "brain": "brain_ct",
    "thorax": "thorax_xray",
}

# 라벨 순서는 학습 때 정한 순서를 그대로 유지한다(판별 모델 출력 순서와 맞춘다).
LABEL_ORDER = ["brain_ct", "brain_mri", "thorax_xray", "knee_mri"]


def label_ko(code: str) -> str:
    return LABELS.get(code, LABELS[UNKNOWN])
