"""
흉부 X-ray 학습 해설.

채점이 끝난 뒤 결과 화면에 보여 줄 "소견 해설"을 둔다. 흉부 문제의 정답(study.st_disease)은
병명이 아니라 영상에 보이는 소견(폐음영·흉막삼출·기흉·심비대)이라, 해설도 소견 기준으로 쓴다.
모델 출력과 상관없는 고정 설명이다. 다만 이 영상의 정답 박스 좌우(한쪽/양쪽)와 촬영 방향(AP/PA)에 맞춰
VIEW_POINTS·SIDE_POINTS 에서 문장을 골라 case_summary 뒤에 붙이고 해당 줄을 앞으로 올린다(새 문장을 지어내지 않는다).

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

NOTE_DISCLAIMER = "학습용 참고 정보예요. 진단이 아니고, 실제 판단은 임상 정보와 함께 의사가 해요."

# 출처. 화면은 label(짧은 이름)을 보여 주고, citation(서지)과 where(원문에서 확인한 표·항목)를 함께 단다.
#   title: 원문 제목 그대로(예전 화면 호환), url: 원문 주소
_BTS_PLEURAL = {"title": "British Thoracic Society Guideline for pleural disease (Thorax 2023)",
                "label": "BTS 흉막질환 지침 2023",
                "citation": "Roberts ME, et al. Thorax 2023;78(Suppl 3):1-42",
                "url": "https://brit-thoracic.org.uk/document-library/guidelines/pleural-disease/bts-guideline-for-pleural-disease"}
_ATS_IDSA_CAP = {"title": "ATS/IDSA Community-acquired Pneumonia Guideline (AJRCCM 2019)",
                 "label": "ATS/IDSA 지역사회획득폐렴 지침 2019",
                 "citation": "Metlay JP, et al. Am J Respir Crit Care Med 2019;200(7):e45-e67",
                 "url": "https://www.atsjournals.org/doi/10.1164/rccm.201908-1581ST"}
_AHA_HF = {"title": "2022 AHA/ACC/HFSA Guideline for the Management of Heart Failure (Circulation 2022)",
           "label": "AHA/ACC/HFSA 심부전 지침 2022",
           "citation": "Heidenreich PA, et al. Circulation 2022;145(18):e895-e1032",
           "url": "https://www.ahajournals.org/doi/10.1161/CIR.0000000000001063"}
_FLEISCHNER = {"title": "Fleischner Society: Glossary of Terms for Thoracic Imaging (Radiology 2024)",
               "label": "Fleischner 흉부영상 용어집 2024",
               "citation": "Bankier AA, et al. Radiology 2024;310(2):e232558",
               "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10902601/"}


def _at(source: dict, where: str) -> dict:
    """출처에 원문 위치(표·항목)를 붙인다. 원문을 읽고 확인한 위치만 쓴다."""
    return {**source, "where": where}


def _step(sign: str, suspect: str, next_: str, source: dict) -> dict:
    """이렇게 보이면 한 줄. 지침·용어집으로 확인한 줄만 만든다."""
    return {"sign": sign, "suspect": suspect, "next": next_, "evidence": "guideline", "source": source}

CONFIRM_NOTE = "X-ray로는 보이는 소견까지만 알 수 있다. 원인은 검사와 증상, 병력을 함께 보고 정한다."

# 글은 학습자 화면에 그대로 나간다. 말투는 역할별로 나눈다.
#   해설(지식): 교과서처럼 "~다." 로 쓴다. 이 파일의 NOTES·COMBINATIONS·VIEW_POINTS·SIDE_POINTS·case_summary 가 여기에 든다.
#   학습자에게 하는 말(채점 안내·맨 아래 안내): "~요." 로 쓴다. NOTE_DISCLAIMER, 채점 응답의 feedback·metrics_text, 화면 안내.
# 한 줄에 한두 문장만 쓰고, 화살표·가운뎃점·대시 같은 기호와 다른 칸을 가리키는 말은 쓰지 않는다.
# pitfall 은 목록으로 두고, 화면용 문자열(pitfall)은 learning_note() 가 이어 붙인다.
NOTES = {
    "pleural_effusion": {
        "summary": "흉막강(폐와 흉벽 사이)에 액체가 고인 상태다.",
        # 판독 포인트: 학회 지침(Fleischner 2024 용어집)으로 확인한 줄만 appearance 에 두고 줄마다 출처를 단다.
        # 아직 지침으로 확인하지 못한 일반 설명은 appearance_extra(검수 전)로 따로 둔다.
        "appearance": [
            "양이 적으면 늑골횡격막각이 뭉툭해질 수 있다.",
            "그 자리가 초승달 모양(메니스커스)으로 보일 수 있다.",
            # BTS 2023: 한쪽(unilateral)과 양쪽(bilateral, 표 4 원인 목록)을 나눠 원인을 찾는다
            "한쪽인지 양쪽인지 확인한다.",
        ],
        "appearance_sources": [[_at(_FLEISCHNER, "Pleura(Effusion) 항목")], [_at(_FLEISCHNER, "Pleura(Effusion) 항목")],
                               [_at(_BTS_PLEURAL, "표 4 양쪽 흉막삼출의 원인")]],
        "appearance_extra": [
            "양이 많으면 종격동이 반대쪽으로 밀린다.",
        ],
        "related": [
            "심부전은 양쪽이나 오른쪽에 잘 생긴다.",
            "폐렴은 같은 쪽 폐음영과 함께 보일 수 있다.",
            "악성 종양은 대개 한쪽에 많이 고이지만, 양이 적을 수도 있다.",
            "결핵에서도 생길 수 있다.",
            "폐색전증에서도 생길 수 있다.",
            "간경화나 신증후군처럼 혈액 단백질이 줄어드는 병에서도 생긴다.",
        ],
        "confirm": [
            "흉부 초음파로 적은 양도 찾고, 흉수를 뽑을 자리를 정한다.",
            "흉수천자로 삼출액인지 여출액인지 나눠 원인을 좁힌다.",
            "흉수천자가 위험하거나 원인을 더 찾아야 하면 흉부 CT를 찍는다. 악성 종양이 의심되면 배와 골반까지 찍는다.",
            "심부전이 의심되면 심초음파로 심장 기능을 본다. 정상이어도 심부전을 완전히 배제하지는 못한다.",
        ],
        "next_steps": [
            _step("한쪽에만 있다.", "감염이나 악성 종양 같은 원인을 찾아야 한다.",
                  "초음파로 보면서 흉수를 뽑아 검사한다.",
                  _at(_BTS_PLEURAL, "미확진 한쪽 흉막삼출 검사, 영상 유도 흉수천자 권고")),
            # 원문: 세포검사를 첫 검사로 하고, 음성이면 추가 검사를 고려한다(흉막 조직검사는 별도 권고)
            _step("한쪽에 있고 악성 종양이 의심된다.", "악성 흉수일 수 있다.",
                  "흉수 세포검사를 먼저 한다. 음성이면 흉막 조직검사 같은 추가 검사를 고려한다.",
                  _at(_BTS_PLEURAL, "흉수 검사 권고, 세포검사를 첫 검사로")),
            _step("같은 쪽에 폐음영이 있고 열이 난다.", "폐렴에 따른 삼출이나 흉막 감염일 수 있다.",
                  "흉수를 뽑아 세균 검사를 한다.",
                  _at(_BTS_PLEURAL, "흉수 검체 권고, 감염 의심 시 혈액배양병에도 담기")),
        ],
        "pitfall": [
            "누워서 찍은 사진에서는 액체가 등 쪽으로 퍼져서 한쪽 폐가 뿌옇게만 보인다. 그래서 놓치기 쉽다.",
        ],
        "references": [
            {"title": "Radiopaedia - Pleural effusion", "url": "https://radiopaedia.org/articles/pleural-effusion"},
        ],
    },
    "airspace_opacity": {
        "summary": "폐포가 액체, 고름, 피, 세포로 차서 하얗게 보이는 부위다. 원인은 위치와 분포, 임상 정보를 같이 보고 판단한다.",
        "appearance": [
            "폐가 있어야 할 자리가 주변보다 하얗게 보인다. 이것만으로는 원인을 알 수 없다.",
            "하얀 음영 속에 검은 기관지가 보일 수 있다. 이를 공기기관지조영이라고 한다.",
            "심장이나 횡격막 경계가 흐려지는 것을 실루엣 징후라고 한다. 맞닿은 구조와 농도가 비슷할 때 생긴다.",
        ],
        "appearance_sources": [[_at(_FLEISCHNER, "Consolidation 항목")], [_at(_FLEISCHNER, "Air bronchogram 항목")],
                               [_at(_FLEISCHNER, "Silhouette sign 항목")]],
        "appearance_extra": [
            "한 엽에 몰려 있는지, 양쪽에 퍼져 있는지 본다.",
        ],
        "related": [
            "폐렴일 수 있다.",
            "심부전 등으로 폐에 물이 찬 폐부종일 수 있다.",
            "폐출혈일 수 있다.",
            "무기폐일 수 있다.",
            "종양일 수 있다.",
        ],
        "confirm": [
            "증상, 진찰과 흉부 X-ray를 함께 본다. 프로칼시토닌 수치만 보고 항생제 시작을 미루지는 않는다.",
            "객담 검사는 중증이거나 입원한 환자에서 한다. 외래 환자는 보통 하지 않는다.",
            "X-ray가 애매하거나 놓쳤을 것 같으면 흉부 CT를 찍는다.",
            "추적 X-ray는 5~7일 안에 좋아지면 보통 찍지 않는다. 좋아지지 않을 때 고려한다.",
        ],
        "next_steps": [
            _step("하얀 음영 속에 검은 기관지가 보인다.", "폐포가 차 있는 경화다. 이것만으로 원인은 알 수 없다.",
                  "증상과 혈액검사를 같이 보고 원인을 판단한다.", _at(_FLEISCHNER, "Air bronchogram 항목")),
            _step("열과 기침이 있어 폐렴이 의심된다.", "폐렴일 수 있다.",
                  "외래 환자는 객담 검사를 보통 하지 않는다. 5~7일 안에 좋아지면 추적 X-ray도 필요 없다.",
                  _at(_ATS_IDSA_CAP, "질문 1 객담 검사, 질문 16 추적 흉부 영상")),
        ],
        "pitfall": [
            "유방이나 근육 같은 연조직이 겹치면 폐음영처럼 보일 수 있다. 양쪽을 비교해 본다.",
        ],
        "references": [
            {"title": "Radiopaedia - Airspace opacification", "url": "https://radiopaedia.org/articles/airspace-opacification"},
        ],
    },
    "pneumothorax": {
        "summary": "흉막강에 공기가 들어가 폐가 쪼그라든 상태다.",
        "appearance": [
            "폐와 공기 사이 경계가 얇은 곡선으로 보인다. 이 선을 흉막선이라고 한다.",
            "긴장성 기흉은 주로 증상과 진찰로 진단한다. 사진에서는 종격동이 반대쪽으로 밀리고 횡격막이 내려간다.",
        ],
        "appearance_sources": [[_at(_FLEISCHNER, "Pneumothorax 항목")], [_at(_FLEISCHNER, "Pneumothorax 항목")]],
        "appearance_extra": [
            "흉막선 바깥에는 폐 혈관 무늬가 보이지 않는다.",
        ],
        "related": [
            "자발성 기흉은 키가 크고 마른 젊은 사람이나 흡연자에게 흔하다.",
            "COPD 같은 기존 폐질환이 있으면 생길 수 있다.",
            "늑골 골절 같은 외상으로 생길 수 있다.",
            "시술이나 인공호흡기 사용 뒤에 생길 수 있다.",
        ],
        "confirm": [
            "대부분 흉부 X-ray로 확인한다. 작거나 애매하면 CT를 쓰기도 한다.",
            "흉부 CT로 작은 기흉을 확인하고, 폐 기포 같은 원인을 찾는다.",
            "증상과 활력징후를 보고 응급인 긴장성 기흉인지 판단한다.",
        ],
        "next_steps": [
            _step("얇은 흉막선이 있고 바깥에 혈관 무늬가 없으며 증상이 적다.", "자발성 기흉일 수 있다.",
                  "증상이 적으면 크기와 상관없이 경과를 지켜볼 수 있다.",
                  _at(_BTS_PLEURAL, "자발성 기흉 급성기 처치 권고")),
        ],
        "pitfall": [
            "피부 주름이나 날개뼈 가장자리를 흉막선으로 잘못 보기 쉽다.",
            "누워서 찍은 사진에서는 늑골횡격막각이 깊게 파여 보이는 것으로만 나타나기도 한다.",
        ],
        "references": [
            {"title": "Radiopaedia - Pneumothorax", "url": "https://radiopaedia.org/articles/pneumothorax"},
        ],
    },
    "cardiomegaly": {
        "summary": "심장 그림자가 커진 상태다.",
        # 심비대는 Fleischner 용어집에 해당 용어가 없어 지침으로 확인된 줄이 아직 없다
        "appearance": [],
        "appearance_sources": [],
        "appearance_extra": [
            "PA 사진에서 심장 폭이 흉곽 폭의 절반보다 크면 커졌다고 본다. 이 비율을 심흉비라고 한다.",
            "폐혈관이 두드러지는지, 흉막삼출이 같이 있는지 본다.",
        ],
        "related": [
            "심부전에서 흔히 보인다.",
            "판막 질환일 수 있다.",
            "심근병증일 수 있다.",
            "심낭삼출이 있으면 심장 주변 액체 때문에 커 보일 수 있다.",
            "고혈압과 관련될 수 있다.",
        ],
        "confirm": [
            "심초음파로 실제로 커졌는지, 심낭삼출 때문인지, 심장 기능은 어떤지 본다.",
            "심전도로 리듬과 심장 부담을 본다.",
            "BNP 같은 혈액검사로 심부전인지 본다.",
        ],
        "next_steps": [
            _step("심장 그림자가 커 보인다.", "심부전 같은 심장 질환일 수 있다. X-ray만으로는 원인을 알 수 없다.",
                  "병력과 진찰 다음 첫 검사는 심초음파다. 심장 구조와 박출률을 확인한다.",
                  _at(_AHA_HF, "4.4 심장 영상 평가")),
            _step("숨이 차는 등 심부전이 의심된다.", "심부전일 수 있다.",
                  "BNP나 NT-proBNP로 확인한다. 두 검사의 기준값은 서로 바꿔 쓰지 않고, 비만이면 낮게 나올 수 있다.",
                  _at(_AHA_HF, "4.2 바이오마커 사용")),
        ],
        "pitfall": [
            "누워서 찍었거나 숨을 덜 들이쉰 사진에서는 심장이 실제보다 커 보인다.",
            "AP 사진에서도 크기가 정상이면 심비대가 아닐 가능성이 높다.",
            "AP 사진은 구석의 AP, PORTABLE 표시, 폐에 겹친 날개뼈, 높고 평평한 쇄골, 튜브나 라인으로 알아볼 수 있다.",
        ],
        "references": [
            {"title": "Radiopaedia - Cardiomegaly", "url": "https://radiopaedia.org/articles/cardiomegaly"},
        ],
    },
}

# 두 소견이 같이 보일 때. (순서 없는 쌍, 설명, 출처 또는 None)
# 출처는 원문을 읽고 확인한 것만 단다(2026-11-16 대조). None 은 화면에 "지침 확인 전"으로 나간다.
#   심비대 + 양쪽 흉막삼출: BTS 2023 표 7 "Cardiac dysfunction: usually bilateral; cardiomegaly; pericardial effusion"
#   폐음영 + 흉막삼출: BTS 2023 "presence of pulmonary consolidation" 은 악성보다 흉막 감염(부폐렴성 삼출·농흉)에 흔하다
#   (CT 소견이고 민감도는 낮다). 폐부종(폐문 주위 음영)·수기흉(공기-액체 경계)은 Fleischner 2024·BTS 2023 에서 찾지 못했다.
# 화면은 "동반 소견" 카드(소견 / 의심 질환 / 출처)로 보여 준다.
COMBINATIONS = [
    ({"cardiomegaly", "pleural_effusion"},
     "심비대와 양쪽 흉막삼출이 같이 보인다.", "심부전을 먼저 생각한다.",
     _at(_BTS_PLEURAL, "표 7 한쪽 흉막삼출의 원인별 동반 영상 소견")),
    ({"cardiomegaly", "airspace_opacity"},
     "심비대와 함께 양쪽 폐문 주위로 음영이 퍼진다.", "폐부종일 수 있다.", None),
    ({"airspace_opacity", "pleural_effusion"},
     "같은 쪽에 폐음영과 흉막삼출이 같이 있다.", "폐렴에 따른 삼출이나 농흉일 수 있다.",
     _at(_BTS_PLEURAL, "흉막 감염 절의 CT 소견")),
    ({"pneumothorax", "pleural_effusion"},
     "공기와 액체가 같이 고여 수평한 경계가 보인다.", "수기흉일 수 있다.", None),
]

# 이 영상에 맞춰 고르는 문장(B안). 새 의학 주장을 만들지 않고, 위 NOTES 의 같은 소견 문장(pitfall·related·next_steps)을
# 이 영상의 조건에 맞춰 가리키기만 한다. 조건은 데이터셋 정답 박스의 좌우(한쪽/양쪽)와 촬영 방향(AP/PA)뿐이다.
# AI 박스로 채점한 문제는 위치가 틀릴 수 있어 좌우 문장을 쓰지 않는다.
VIEW_POINTS = {
    "cardiomegaly": {
        "AP": "AP 사진이라 심장이 실제보다 커 보일 수 있다.",
        "PA": "PA 사진이라 심장 폭이 흉곽 폭의 절반을 넘는지로 크기를 볼 수 있다.",
    },
    "pleural_effusion": {
        "AP": "AP 사진이라 누워서 찍었다면 액체가 퍼져 뿌옇게만 보일 수 있다.",
    },
    "pneumothorax": {
        "AP": "AP 사진이라 누워서 찍었다면 흉막선 대신 늑골횡격막각이 깊게 파여 보일 수 있다.",
    },
}
# 좌우: (문장, 앞으로 올릴 related 머리말, 앞으로 올릴 next_steps 의 sign)
SIDE_POINTS = {
    "pleural_effusion": {
        "one": ("한쪽에만 있으면 감염이나 악성 종양 같은 원인을 찾아야 한다.", None, "한쪽에만 있다."),
        "both": ("양쪽에 있으면 심부전을 먼저 생각한다.", "심부전", None),
    },
}

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
    """이 영상의 의심 부위 한 줄. 모델이 지어내는 글이 아니라 채점한 박스의 위치·개수·크기만 쓴다.
    예) "하부 환자 우측(중간 범위)에 1곳 있다. 한쪽에만 있다."
    name 은 예전 호출과 맞추려고 받는다(문장에는 안 씀)."""
    from .localization import describe_box

    boxes = [t for t in targets or [] if t.get("width") and t.get("height")]
    if not boxes:
        return ""
    parts, sides = [], set()
    for t in boxes:
        place = describe_box(t["x"], t["y"], t["width"], t["height"])
        sides.add(place.split("-")[-1])
        parts.append(f"{place.replace('-', ' ')}({_area_word(t['width'] * t['height'])})")
    lr = {s for s in sides if s in ("환자 우측", "환자 좌측")}
    if len(lr) == 2:
        side_text = "양쪽에 있다."
    elif lr:
        side_text = "한쪽에만 있다."
    else:
        side_text = "가운데에 있다."
    return f"{', '.join(parts)}에 {len(parts)}곳 있다. {side_text}"


def _side_count(targets: list[dict] | None) -> str | None:
    """정답 박스들의 좌우 → "one"(한쪽) / "both"(양쪽) / None(가운데뿐이거나 박스 없음)."""
    from .localization import describe_box

    sides = set()
    for t in targets or []:
        if t.get("width") and t.get("height"):
            side = describe_box(t["x"], t["y"], t["width"], t["height"]).split("-")[-1]
            if side in ("환자 우측", "환자 좌측"):
                sides.add(side)
    if not sides:
        return None
    return "both" if len(sides) == 2 else "one"


def case_points(code: str, targets: list[dict] | None, view: str | None, target_source: str | None) -> dict:
    """이 영상에 맞춰 고른 문장과 앞으로 올릴 줄. {"points": [...], "related_first": str|None, "step_first": str|None}"""
    points, related_first, step_first = [], None, None
    if target_source == "dataset":
        side = _side_count(targets)
        rule = SIDE_POINTS.get(code, {}).get(side) if side else None
        if rule:
            points.append(rule[0])
            related_first, step_first = rule[1], rule[2]
    view_text = VIEW_POINTS.get(code, {}).get(view or "")
    if view_text:
        points.append(view_text)
    return {"points": points, "related_first": related_first, "step_first": step_first}


def _first(items: list, match) -> list:
    """match 가 참인 첫 항목을 맨 앞으로. 없으면 그대로."""
    for i, item in enumerate(items):
        if match(item):
            return [item] + items[:i] + items[i + 1:]
    return items


def learning_note(disease: str | None, targets: list[dict] | None = None, *,
                  view: str | None = None, target_source: str | None = None) -> dict | None:
    """정답 소견으로 결과 화면용 해설을 만든다. targets(채점 응답의 의심 부위, 0~1 비율 박스)가 있으면
    "이 영상에서는" 위치를 채운다. view(AP/PA)·target_source(dataset/ai)로 이 영상에 맞는 문장을 골라
    case_summary 뒤에 붙이고, 해당하는 관련 질환·다음 단계 줄을 앞으로 올린다. 모르는 이름이면 None."""
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
    picked = case_points(code, targets, view, target_source)
    case_lines = [line for line in [case_summary(class_ko(code), targets)] + picked["points"] if line]
    pitfall_lines = list(note["pitfall"])
    related = list(note["related"])
    if picked["related_first"]:
        related = _first(related, lambda line: line.startswith(picked["related_first"]))
    steps = [{**step, "source": dict(step["source"])} for step in note.get("next_steps", [])]
    if picked["step_first"]:
        steps = _first(steps, lambda step: step["sign"] == picked["step_first"])
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
        # 화면은 목록(case_lines·pitfall_lines)을 한 줄씩 보여 준다. 문자열은 예전 화면·RAG 와 맞추려고 같이 둔다
        "case_summary": " ".join(case_lines),
        "case_lines": case_lines,
        "related": related,
        "confirm": list(note.get("confirm", [])),
        "confirm_note": CONFIRM_NOTE,
        "combinations": [f"{sign} {suspect}" for pair, sign, suspect, _ in COMBINATIONS if code in pair],
        # 화면용 "동반 소견" 카드: 소견·의심 질환·출처(없으면 None = 지침 확인 전)
        "combination_items": [{"sign": sign, "suspect": suspect, "text": f"{sign} {suspect}",
                               "source": dict(src) if src else None}
                              for pair, sign, suspect, src in COMBINATIONS if code in pair],
        "next_steps": steps,
        "pitfall": " ".join(pitfall_lines),
        "pitfall_lines": pitfall_lines,
        "references": [dict(ref) for ref in note["references"]],
        "disclaimer": NOTE_DISCLAIMER,
    }
