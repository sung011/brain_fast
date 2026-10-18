"""
흉부 X-ray 학습 해설.

채점이 끝난 뒤 결과 화면에 보여 줄 "이 소견은 무엇이고, 어떤 질환과 이어질 수 있는가"를 둔다.
모델 출력과 상관없는 고정 설명이다. 문제의 정답 병명(st_disease)으로 고른다.

- NOTES: 소견 코드별 설명, 관련 질환, 같이 볼 점, 헷갈리기 쉬운 점, 참고 문서
- COMBINATIONS: 두 소견이 같이 보일 때 생각해 볼 상황
- learning_note(): 정답 병명 → 화면용 dict. 모르는 병명이면 None

교과서 수준의 일반적인 내용만 적고, 모든 문장은 단정하지 않는다(진단이 아니다).
글을 고칠 때는 이 파일만 고치면 된다.
"""
from __future__ import annotations

from .constants import LABELS, class_ko, class_label, finding_code

NOTE_DISCLAIMER = "학습용 참고 정보이며 진단이 아닙니다. 실제 판단은 임상 정보와 함께 의사가 합니다."

NOTES = {
    "pleural_effusion": {
        "summary": (
            "흉막강(폐와 흉벽 사이)에 액체가 고인 상태입니다. 서서 찍은 정면(PA) 사진에서는 "
            "늑골횡격막각이 둔해지고, 바깥쪽이 위로 올라가는 오목한 경계(메니스커스 징후)가 보입니다."
        ),
        "related": [
            "심부전 — 양쪽에 생기는 경우가 많습니다",
            "폐렴 — 같은 쪽 폐음영과 함께 보일 수 있습니다(부폐렴성 삼출)",
            "악성 종양 — 한쪽에 많은 양이 고일 수 있습니다",
            "결핵",
            "폐색전증",
            "간경화·신증후군처럼 혈액 속 단백질이 낮아지는 질환",
        ],
        "check": [
            "한쪽인지 양쪽인지",
            "늑골횡격막각이 둔해졌는지",
            "양이 많으면 종격동이 반대쪽으로 밀렸는지",
        ],
        "pitfall": "누워서 찍은(AP) 사진에서는 액체가 등 쪽으로 퍼져 한쪽 폐가 전체적으로 뿌옇게만 보여 놓치기 쉽습니다.",
        "references": [
            {"title": "Radiopaedia - Pleural effusion", "url": "https://radiopaedia.org/articles/pleural-effusion"},
        ],
    },
    "airspace_opacity": {
        "summary": (
            "폐포 안의 공기가 액체·고름·피·세포 등으로 채워져 하얗게 보이는 부위입니다. "
            "음영 자체는 결과이고, 원인은 위치·분포와 임상 정보를 함께 보고 생각합니다."
        ),
        "related": [
            "폐렴",
            "폐부종 — 심부전 등으로 폐에 물이 찬 경우",
            "폐출혈",
            "무기폐",
            "종양",
        ],
        "check": [
            "한 부위(엽)에 몰려 있는지, 양쪽에 퍼져 있는지",
            "하얀 음영 속에 검은 기관지가 보이는지(공기기관지조영)",
            "심장·횡격막 경계가 지워졌는지(실루엣 징후) — 위치를 짐작하는 데 씁니다",
        ],
        "pitfall": "유방·근육 같은 연조직이 겹쳐 폐음영처럼 보일 수 있습니다. 양쪽을 비교해 봅니다.",
        "references": [
            {"title": "Radiopaedia - Airspace opacification", "url": "https://radiopaedia.org/articles/airspace-opacification"},
        ],
    },
    "pneumothorax": {
        "summary": (
            "흉막강에 공기가 들어가 폐가 쪼그라든 상태입니다. 얇은 흰 선(흉막선)이 보이고, "
            "그 바깥쪽으로는 폐 혈관 무늬가 보이지 않습니다."
        ),
        "related": [
            "자발성 기흉 — 키가 크고 마른 젊은 남성, 흡연과 관련될 수 있습니다",
            "COPD 등 기존 폐질환",
            "외상 — 늑골 골절 등",
            "시술·인공호흡기 사용 후(의인성)",
        ],
        "check": [
            "흉막선과 그 바깥의 혈관 무늬 소실",
            "기관·종격동이 반대쪽으로 밀렸는지 — 긴장성 기흉이면 응급입니다",
            "폐 꼭대기(폐첨부)를 꼼꼼히 볼 것",
        ],
        "pitfall": "피부 주름이나 견갑골 가장자리를 흉막선으로 착각하기 쉽습니다. 누운 사진에서는 늑골횡격막각이 깊게 파여 보이는 것(deep sulcus sign)으로 나타나기도 합니다.",
        "references": [
            {"title": "Radiopaedia - Pneumothorax", "url": "https://radiopaedia.org/articles/pneumothorax"},
        ],
    },
    "cardiomegaly": {
        "summary": (
            "심장 그림자가 커진 상태입니다. 정면(PA) 사진에서 심장의 최대 폭이 흉곽 최대 폭의 "
            "절반보다 크면(심흉비 > 0.5) 심비대로 봅니다."
        ),
        "related": [
            "심부전",
            "판막 질환",
            "심근병증",
            "심낭삼출 — 심장 자체가 아니라 주변 액체 때문에 커 보일 수 있습니다",
            "고혈압",
        ],
        "check": [
            "심흉비(심장 폭 ÷ 흉곽 폭)",
            "폐혈관이 두드러지거나 흉막삼출이 같이 있는지",
        ],
        "pitfall": "누워서 찍은(AP) 사진이나 숨을 덜 들이쉰 사진에서는 심장이 실제보다 크게 보입니다.",
        "references": [
            {"title": "Radiopaedia - Cardiomegaly", "url": "https://radiopaedia.org/articles/cardiomegaly"},
        ],
    },
}

# 두 소견이 같이 보일 때. 순서 없는 쌍 → 설명
COMBINATIONS = [
    ({"cardiomegaly", "pleural_effusion"},
     "심비대와 양쪽 흉막삼출이 같이 보이면 심부전을 먼저 생각해 볼 수 있습니다."),
    ({"cardiomegaly", "airspace_opacity"},
     "심비대와 함께 양쪽 폐문 주위로 퍼진 음영이 보이면 폐부종(심부전)을 생각해 볼 수 있습니다."),
    ({"airspace_opacity", "pleural_effusion"},
     "같은 쪽에 폐음영과 흉막삼출이 같이 보이면 폐렴에 동반된 삼출을 생각해 볼 수 있습니다."),
    ({"pneumothorax", "pleural_effusion"},
     "공기와 액체가 함께 고이면 수평한 공기-액체 경계(수기흉)가 보일 수 있습니다."),
]

# 한국어 병명 → 소견 코드 (study.st_disease 는 한국어로 들어온다)
_KO_TO_CODE = {names["ko"]: code for code, names in LABELS.items() if "ko" in names}


def disease_code(disease: str | None) -> str | None:
    """정답 병명(한국어 또는 영문) → 소견 코드. 해설이 없는 병명이면 None."""
    if not disease or not str(disease).strip():
        return None
    name = str(disease).strip()
    code = _KO_TO_CODE.get(name) or finding_code(name)
    return code if code in NOTES else None


def learning_note(disease: str | None) -> dict | None:
    """정답 병명으로 결과 화면용 해설을 만든다. 모르는 병명이면 None."""
    code = disease_code(disease)
    if code is None:
        return None
    note = NOTES[code]
    return {
        "code": code,
        "name": class_ko(code),
        "name_en": class_label(code, "en"),
        "summary": note["summary"],
        "related": list(note["related"]),
        "check": list(note["check"]),
        "combinations": [text for pair, text in COMBINATIONS if code in pair],
        "pitfall": note["pitfall"],
        "references": [dict(ref) for ref in note["references"]],
        "disclaimer": NOTE_DISCLAIMER,
    }
