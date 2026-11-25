"""
흉부 학습 문제의 의사 정답 박스·촬영 방향(AP/PA) 표.

- 값은 같은 폴더의 answer_boxes.json 에 있다. NIH ChestX-ray14 BBox_List_2017(전문의가 그린 박스)을
  우리 문제 목록(problems.csv v1)에서 옮긴 것이다. DB 가 아니라 파일에 두므로 팀 DB 를 바꾸지 않는다.
- 사진 파일 이름(예: 00000032_037.png)으로 찾는다. 관리자 등록이 붙이는 "<10자리>_" 접두어는 떼고 찾는다.
- 표에 없으면 None 이다. 그때 흉부 채점은 예전처럼 AI 박스를 기준으로 한다.
- 정답 박스는 데이터셋 기준이지 진단이 아니다. 학습용 보조다.
- "같이 있는 소견"(also): 문제의 대표 소견 말고 같은 사진에 실제로 있는 4소견. 채점에서 틀린 것으로 보지 않고 안내만 한다.
  ChestX-Det 문제(problems.csv 의 also_findings 열)는 의사 라벨이고 박스도 있다. NIH 문제는 NIH 원래 라벨
  (Data_Entry_2017 의 Finding Labels, 판독문에서 자동 추출)로 채우고 박스는 없다.

다시 만들기 (problems.csv 가 바뀌었을 때, 저장소 루트에서. 여러 문제 묶음을 한 번에):
    python -c "from importlib import import_module as m; m('class.thorax.answers').build_json([r'<v1 problems.csv>', r'<v2 problems.csv>'], nih_labels_csv=r'<Data_Entry_2017_v2020.csv>')"
"미리보기 1차 검토"에서 "제외"·"확인 필요" 로 적힌 줄은 넣지 않는다(박스 위치가 이상해 보인 사진).
"""
from __future__ import annotations

import csv
import json
import math
import re
from functools import lru_cache
from pathlib import Path

ANSWER_FILE = Path(__file__).with_name("answer_boxes.json")
SOURCE = "NIH ChestX-ray14 BBox_List_2017 (Wang et al., CVPR 2017)"
VIEWS = ("AP", "PA")
FINDINGS = ("폐음영", "흉막삼출", "기흉", "심비대")
# NIH 원래 라벨 → 우리 소견 이름 (문제 목록을 만들 때와 같은 묶음: 침윤·경화·폐렴은 폐음영)
NIH_LABELS = {"Infiltration": "폐음영", "Consolidation": "폐음영", "Pneumonia": "폐음영",
              "Effusion": "흉막삼출", "Pneumothorax": "기흉", "Cardiomegaly": "심비대"}
_UPLOAD_PREFIX = re.compile(r"^[0-9a-f]{10}_")


def image_key(st_image: str | None) -> str:
    """study.st_image(웹 경로, 슬라이드면 JSON 배열) → 원래 사진 파일 이름."""
    text = (st_image or "").strip()
    if text.startswith("["):
        try:
            items = json.loads(text)
            text = str(items[0]) if items else ""
        except ValueError:
            pass
    name = text.replace("\\", "/").split("/")[-1]
    return _UPLOAD_PREFIX.sub("", name)


def _valid_boxes(boxes) -> bool:
    if not isinstance(boxes, list) or not boxes:
        return False
    for box in boxes:
        if not isinstance(box, dict):
            return False
        try:
            x, y, w, h = (float(box[k]) for k in ("x", "y", "width", "height"))
        except (KeyError, TypeError, ValueError):
            return False
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0 or x < 0 or y < 0:
            return False
        if x + w > 1 + 1e-6 or y + h > 1 + 1e-6:
            return False
    return True


@lru_cache(maxsize=1)
def _table() -> dict[str, dict]:
    if not ANSWER_FILE.is_file():
        return {}
    with open(ANSWER_FILE, encoding="utf-8") as f:
        return json.load(f).get("answers", {})


def find_answer(st_image: str | None, st_disease: str | None = None) -> dict | None:
    """사진 이름으로 정답을 찾는다. {"finding", "view", "boxes", "also", "also_boxes"} 또는 None.
    st_disease 를 주면 표의 정답 소견과 같을 때만 돌려준다(관리자가 병명을 바꾼 문제를 막는다).
    boxes 는 대표 소견 박스만, also_boxes 는 같이 있는 소견의 박스(있을 때만, NIH 문제는 빈 목록)."""
    item = _table().get(image_key(st_image))
    if not item:
        return None
    finding = item.get("finding")
    if st_disease is not None and (st_disease or "").strip() != finding:
        return None
    if not _valid_boxes(item.get("boxes")):
        return None
    view = item.get("view")
    also = [f for f in (item.get("also") or []) if f in FINDINGS and f != finding]
    main = [dict(b) for b in item["boxes"] if b.get("finding") in (None, "", finding)]
    extra = [dict(b) for b in item["boxes"] if b.get("finding") in also]
    return {"finding": finding, "view": view if view in VIEWS else None,
            "boxes": main or [dict(b) for b in item["boxes"]], "also": also, "also_boxes": extra}


def _nih_also(csv_path: str | Path) -> dict[str, list[str]]:
    """Data_Entry_2017 → {사진 이름: [우리 4소견 이름]} (NIH 원래 라벨, 판독문 자동 추출)."""
    out: dict[str, list[str]] = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            names = {NIH_LABELS[x] for x in (row.get("Finding Labels") or "").split("|") if x in NIH_LABELS}
            if names:
                out[row["Image Index"].strip()] = sorted(names, key=FINDINGS.index)
    return out


def build_json(csv_paths, out_path: str | Path = ANSWER_FILE, nih_labels_csv: str | Path | None = None) -> dict:
    """problems.csv(하나 또는 여러 개) → answer_boxes.json. 넣은 수·뺀 수를 돌려준다.
    also_findings 열("흉막삼출|심비대")이 있으면 그 값을, 없고 nih_labels_csv 를 주면 NIH 원래 라벨로 같이 있는 소견을 채운다."""
    if isinstance(csv_paths, (str, Path)):
        csv_paths = [csv_paths]
    nih = _nih_also(nih_labels_csv) if nih_labels_csv else {}
    answers, skipped = {}, {"검토 메모(제외·확인 필요)": 0, "박스 형식 오류": 0}
    for csv_path in csv_paths:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                note = (row.get("review_note") or "").strip()
                if note.startswith("제외") or note.startswith("확인 필요"):
                    skipped["검토 메모(제외·확인 필요)"] += 1
                    continue
                try:
                    boxes = json.loads(row.get("answer_boxes") or "")
                except ValueError:
                    boxes = None
                if not _valid_boxes(boxes):
                    skipped["박스 형식 오류"] += 1
                    continue
                name = row["image_file"].strip()
                finding = (row.get("st_disease") or "").strip()
                if "also_findings" in row:
                    also = [x.strip() for x in (row.get("also_findings") or "").split("|") if x.strip()]
                else:
                    also = nih.get(name, [])
                view = (row.get("view_position") or "").strip().upper()
                answers[name] = {
                    "finding": finding,
                    "view": view if view in VIEWS else None,
                    "boxes": boxes,
                    "also": [x for x in also if x in FINDINGS and x != finding],
                }
    data = {"source": SOURCE if len(csv_paths) == 1 else SOURCE + " + ChestX-Det (Deepwise, 전문의 3명)",
            "built_from": ", ".join(Path(p).name for p in csv_paths), "count": len(answers), "answers": answers}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    _table.cache_clear()
    return {"넣음": len(answers), **skipped}
