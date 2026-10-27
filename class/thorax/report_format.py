"""
흉부 X-ray 학습 해설.

채점이 끝난 뒤 결과 화면에 보여 줄 "소견 해설"을 둔다. 흉부 문제의 정답(study.st_disease)은
병명이 아니라 영상에 보이는 소견(폐음영·흉막삼출·기흉·심비대)이라, 해설도 소견 기준으로 쓴다.
모델 출력과 상관없는 고정 설명이다.

해설 순서: 이 소견은(summary) → 판독 포인트(appearance, 이 소견이면 보통 이렇게 보인다)
→ 이 영상에서는(case_summary·locations, 채점한 의심 부위로 문제마다 만든다)
→ 감별할 질환(related) → 확인하려면(confirm, 원인을 확인하는 검사) → 다른 소견과 같이 보이면(combinations)
→ 헷갈리기 쉬운 점(pitfall). X-ray 는 소견까지만 보여 주고 원인 질환을 확진하지 못하므로 CONFIRM_NOTE 를 함께 둔다.
이렇게 보이면(next_steps)은 "보이는 것 → 의심 → 다음 검사"를 잇는 줄로, 학회 진료지침·용어집으로 확인한 줄만 넣고
줄마다 출처를 단다(evidence="guideline"). 근거를 못 찾은 줄은 넣지 않는다.

- NOTES: 소견 코드별 설명
- COMBINATIONS: 두 소견이 같이 보일 때 생각해 볼 상황
- learning_note(): 정답 소견 → 화면용 dict. 모르는 이름이면 None

교과서 수준의 일반적인 내용만 적고, 모든 문장은 단정하지 않는다(진단이 아니다).
글을 고칠 때는 이 파일만 고치면 된다.
"""
from __future__ import annotations

from .constants import LABELS, class_ko, class_label, finding_code

NOTE_DISCLAIMER = "의학 검토를 거치지 않은 학습용 참고 자료이며, 진단이 아닙니다."

# 이렇게 보이면(next_steps) 출처
_BTS_PLEURAL = {"title": "British Thoracic Society Guideline for pleural disease (Thorax 2023)",
                "url": "https://brit-thoracic.org.uk/document-library/guidelines/pleural-disease/bts-guideline-for-pleural-disease"}
_ATS_IDSA_CAP = {"title": "ATS/IDSA Community-acquired Pneumonia Guideline (AJRCCM 2019)",
                 "url": "https://www.atsjournals.org/doi/10.1164/rccm.201908-1581ST"}
_AHA_HF = {"title": "2022 AHA/ACC/HFSA Guideline for the Management of Heart Failure (Circulation 2022)",
           "url": "https://www.ahajournals.org/doi/10.1161/CIR.0000000000001063"}
_FLEISCHNER = {"title": "Fleischner Society: Glossary of Terms for Thoracic Imaging (Radiology 2024)",
               "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10902601/"}


def _step(sign: str, suspect: str, next_: str, source: dict) -> dict:
    """이렇게 보이면 한 줄. 지침·용어집으로 확인한 줄만 만든다."""
    return {"sign": sign, "suspect": suspect, "next": next_, "evidence": "guideline", "source": source}

CONFIRM_NOTE = "X-ray 는 보이는 소견까지만 알려 줍니다. 원인 질환은 아래 검사와 증상·병력을 함께 보고 정합니다."

NOTES = {
    "pleural_effusion": {
        "summary": (
            "흉막강(폐와 흉벽 사이)에 액체가 고인 상태입니다."
        ),
        # 판독 포인트: 학회 지침(Fleischner 2024 용어집)으로 확인한 줄만 appearance 에 두고 줄마다 출처를 단다.
        # 아직 지침으로 확인하지 못한 일반 설명은 appearance_extra(검수 전)로 따로 둔다.
        "appearance": [
            "작은 양의 흉수는 늑골횡격막각이 뭉툭해지는 것으로 보일 수 있습니다",
            "그 부위가 초승달(메니스커스) 모양으로 보일 수 있습니다",
        ],
        "appearance_sources": [[_FLEISCHNER], [_FLEISCHNER]],
        "appearance_extra": [
            "한쪽인지 양쪽인지 봅니다",
            "양이 많으면 종격동이 반대쪽으로 밀립니다",
        ],
        "related": [
            "심부전 — 양쪽에 생기는 경우가 많습니다",
            "폐렴 — 같은 쪽 폐음영과 함께 보일 수 있습니다(부폐렴성 삼출)",
            "악성 종양 — 한쪽에 많은 양이 고일 수 있습니다",
            "결핵",
            "폐색전증",
            "간경화·신증후군처럼 혈액 속 단백질이 낮아지는 질환",
        ],
        "confirm": [
            "흉부 초음파 — 적은 양도 확인하고, 흉수를 뽑을 위치를 정합니다",
            "흉수천자(흉수 검사) — 삼출액인지 여출액인지 나눠 원인을 좁힙니다",
            "흉부 CT — 폐·흉막의 원인 병변(종양·폐렴 등)을 찾습니다",
            "심초음파·혈액검사 — 심부전이 의심될 때",
        ],
        "next_steps": [
            _step("한쪽에만 있음", "감염·악성 종양 등 원인을 찾아야 함",
                  "초음파로 위치를 보며 흉수를 뽑아 검사합니다", _BTS_PLEURAL),
            _step("한쪽에 있고 악성 종양이 의심됨", "악성 흉수",
                  "흉수 세포검사를 먼저 합니다. 음성이어도 CT·조직검사 같은 추가 검사를 생각합니다", _BTS_PLEURAL),
            _step("같은 쪽에 폐음영이 있고 열이 남", "폐렴에 동반된 삼출·흉막 감염",
                  "흉수를 뽑아 세균 검사를 합니다", _BTS_PLEURAL),
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
        "appearance": [
            "폐가 있어야 할 자리가 주변보다 하얗게 보입니다. 원인은 이것만으로 알 수 없습니다",
            "하얀 음영 속에 검은 기관지가 보일 수 있습니다(공기기관지조영)",
            "심장·횡격막 같은 경계가 흐려지는 것을 실루엣 징후라고 합니다. 맞닿은 구조의 농도가 비슷할 때 나타납니다",
        ],
        "appearance_sources": [[_FLEISCHNER], [_FLEISCHNER], [_FLEISCHNER]],
        "appearance_extra": [
            "한 부위(엽)에 몰려 있는지, 양쪽에 퍼져 있는지 봅니다",
        ],
        "related": [
            "폐렴",
            "폐부종 — 심부전 등으로 폐에 물이 찬 경우",
            "폐출혈",
            "무기폐",
            "종양",
        ],
        "confirm": [
            "증상·혈액검사(염증 수치) — 감염(폐렴)인지 판단합니다",
            "객담 검사 — 원인균을 찾습니다",
            "흉부 CT — 분포와 원인을 더 자세히 봅니다",
            "추적 X-ray — 치료 뒤 좋아지는지 확인합니다",
        ],
        "next_steps": [
            _step("하얀 음영 안에 검은 기관지가 보임(공기기관지조영)", "폐포가 액체 등으로 채워진 경화. 이것만으로 원인은 알 수 없음",
                  "증상·혈액검사 같은 임상 정보와 함께 원인을 판단합니다", _FLEISCHNER),
            _step("열·기침이 있어 폐렴이 의심됨", "폐렴",
                  "외래 환자는 객담 검사를 일상적으로 하지 않고, 5~7일 안에 좋아지면 추적 X-ray 도 필요하지 않습니다",
                  _ATS_IDSA_CAP),
        ],
        "pitfall": "유방·근육 같은 연조직이 겹쳐 폐음영처럼 보일 수 있습니다. 양쪽을 비교해 봅니다.",
        "references": [
            {"title": "Radiopaedia - Airspace opacification", "url": "https://radiopaedia.org/articles/airspace-opacification"},
        ],
    },
    "pneumothorax": {
        "summary": (
            "흉막강에 공기가 들어가 폐가 쪼그라든 상태입니다."
        ),
        "appearance": [
            "폐와 흉막강 속 공기의 경계가 얇은 곡선(흉막선)으로 보입니다",
            "긴장성 기흉은 주로 임상 소견으로 진단하며, 영상에서는 반대쪽 종격동 이동과 횡격막 하강이 흔히 동반됩니다",
        ],
        "appearance_sources": [[_FLEISCHNER], [_FLEISCHNER]],
        "appearance_extra": [
            "그 선 바깥쪽에는 폐 혈관 무늬가 보이지 않습니다",
            "폐 꼭대기(폐첨부)에서 먼저 보이는 경우가 많습니다",
        ],
        "related": [
            "자발성 기흉 — 키가 크고 마른 젊은 남성, 흡연과 관련될 수 있습니다",
            "COPD 등 기존 폐질환",
            "외상 — 늑골 골절 등",
            "시술·인공호흡기 사용 후(의인성)",
        ],
        "confirm": [
            "기흉 자체는 X-ray 로 진단하는 경우가 많습니다",
            "흉부 CT — 작은 기흉을 확인하고, 폐 기포 같은 원인을 찾습니다",
            "증상·활력징후 — 긴장성 기흉(응급)인지 판단합니다",
        ],
        "next_steps": [
            _step("얇은 흉막선이 있고 바깥에 혈관 무늬가 없음, 증상이 적음", "자발성 기흉",
                  "증상이 적으면 크기와 관계없이 경과 관찰을 고려할 수 있습니다", _BTS_PLEURAL),
        ],
        "pitfall": "피부 주름이나 견갑골 가장자리를 흉막선으로 착각하기 쉽습니다. 누운 사진에서는 늑골횡격막각이 깊게 파여 보이는 것(deep sulcus sign)으로 나타나기도 합니다.",
        "references": [
            {"title": "Radiopaedia - Pneumothorax", "url": "https://radiopaedia.org/articles/pneumothorax"},
        ],
    },
    "cardiomegaly": {
        "summary": (
            "심장 그림자가 커진 상태입니다."
        ),
        # 심비대는 Fleischner 용어집에 해당 용어가 없어 지침으로 확인된 줄이 아직 없다
        "appearance": [],
        "appearance_sources": [],
        "appearance_extra": [
            "정면(PA) 사진에서 심장 최대 폭이 흉곽 최대 폭의 절반보다 큽니다(심흉비 > 0.5)",
            "폐혈관이 두드러지거나 흉막삼출이 같이 있는지 봅니다",
        ],
        "related": [
            "심부전",
            "판막 질환",
            "심근병증",
            "심낭삼출 — 심장 자체가 아니라 주변 액체 때문에 커 보일 수 있습니다",
            "고혈압",
        ],
        "confirm": [
            "심초음파 — 심장이 실제로 커졌는지, 심낭삼출 때문인지, 심장 기능은 어떤지 봅니다",
            "심전도 — 리듬과 심장 부담을 봅니다",
            "혈액검사(BNP 등) — 심부전 여부를 봅니다",
        ],
        "next_steps": [
            _step("심장 그림자가 커 보임", "심부전 등 심장 질환. X-ray 만으로 원인은 알 수 없음",
                  "병력·진찰 뒤에는 심초음파가 가장 유용한 첫 검사입니다. 심장 구조와 박출률(LVEF)을 확인합니다", _AHA_HF),
            _step("숨이 차는 등 심부전이 의심됨", "심부전",
                  "BNP 또는 NT-proBNP 혈액검사로 심부전 여부를 가립니다. 두 검사의 기준값은 서로 바꿔 쓰지 않고, 비만하면 낮게 나올 수 있습니다",
                  _AHA_HF),
        ],
        "pitfall": (
            "누워서 찍은(AP) 사진이나 숨을 덜 들이쉰 사진에서는 심장이 실제보다 크게 보입니다. "
            "그래서 AP 사진에서 심장이 조금 커 보이는 정도로는 심비대라고 하지 않고, 정상 크기로 보일 때만 정상이라고 믿습니다. "
            "AP인지는 사진 구석의 \"AP·PORTABLE\" 표시, 폐 위에 겹친 날개뼈(견갑골), 수평으로 높이 올라간 쇄골, "
            "작은 폐와 높은 횡격막, 심전도 선·튜브 같은 중환자 장비로 알아볼 수 있습니다. "
            "AI도 같은 이유로 AP 사진의 정상 심장을 심비대로 표시할 수 있습니다."
        ),
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
    """정답(한국어 또는 영문 소견 이름) → 소견 코드. 해설이 없는 이름이면 None."""
    if not disease or not str(disease).strip():
        return None
    name = str(disease).strip()
    code = _KO_TO_CODE.get(name) or finding_code(name)
    return code if code in NOTES else None


def _area_word(ratio: float) -> str:
    """박스 넓이(영상 대비) → 크기 말."""
    if ratio < 0.03:
        return "작은 범위"
    if ratio < 0.1:
        return "중간 범위"
    return "넓은 범위"


def case_summary(name: str, targets: list[dict] | None) -> str:
    """이 영상의 의심 부위로 만드는 한두 문장. 모델이 지어내는 글이 아니라 채점한 박스의 위치·개수·크기만 쓴다."""
    from .localization import describe_box

    boxes = [t for t in targets or [] if t.get("width") and t.get("height")]
    if not boxes:
        return ""
    parts = []
    for t in boxes:
        place = describe_box(t["x"], t["y"], t["width"], t["height"])
        parts.append(f"{place}({_area_word(t['width'] * t['height'])})")
    sides = {p.split("-")[-1].split("(")[0] for p in parts}
    lr = {s for s in sides if s in ("환자 우측", "환자 좌측")}
    if len(lr) == 2:
        side_text = "양쪽에 있습니다."
    elif lr:
        side_text = "한쪽에만 있습니다."
    else:
        side_text = "가운데에 있습니다."
    return f"이 영상에서는 {name} 의심 부위가 {len(parts)}곳 있습니다: {', '.join(parts)}. {side_text}"


def learning_note(disease: str | None, targets: list[dict] | None = None) -> dict | None:
    """정답 소견으로 결과 화면용 해설을 만든다. targets(채점 응답의 의심 부위, 0~1 비율 박스)가 있으면
    "이 영상에서는" 위치를 채운다. 모르는 이름이면 None."""
    from .localization import describe_box

    code = disease_code(disease)
    if code is None:
        return None
    note = NOTES[code]
    locations = []
    for t in targets or []:
        place = describe_box(t["x"], t["y"], t["width"], t["height"])
        if place not in locations:
            locations.append(place)
    return {
        "kind": "finding",
        "code": code,
        "name": class_ko(code),
        "name_en": class_label(code, "en"),
        "summary": note["summary"],
        "appearance": list(note["appearance"]),
        "appearance_sources": [[dict(src) for src in line] for line in note.get("appearance_sources", [])],
        "appearance_extra": list(note.get("appearance_extra", [])),
        "locations": locations,
        "case_summary": case_summary(class_ko(code), targets),
        "related": list(note["related"]),
        "confirm": list(note.get("confirm", [])),
        "confirm_note": CONFIRM_NOTE,
        "combinations": [text for pair, text in COMBINATIONS if code in pair],
        "next_steps": [{**step, "source": dict(step["source"])} for step in note.get("next_steps", [])],
        "pitfall": note["pitfall"],
        "references": [dict(ref) for ref in note["references"]],
        "disclaimer": NOTE_DISCLAIMER,
    }
