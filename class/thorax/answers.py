"""
흉부 학습 문제의 의사 정답 박스·촬영 방향(AP/PA) 표.

- 값은 같은 폴더의 answer_boxes.json 에 있다. NIH ChestX-ray14 BBox_List_2017(전문의가 그린 박스)을
  우리 문제 목록(problems.csv v1)에서 옮긴 것이다. DB 가 아니라 파일에 두므로 팀 DB 를 바꾸지 않는다.
- 사진 파일 이름(예: 00000032_037.png)으로 찾는다. 관리자 등록이 붙이는 "<10자리>_" 접두어는 떼고 찾는다.
- 표에 없으면 None 이다. 그때 흉부 채점은 예전처럼 AI 박스를 기준으로 한다.
- 정답 박스는 데이터셋 기준이지 진단이 아니다. 학습용 보조다.

다시 만들기 (problems.csv 가 바뀌었을 때, 저장소 루트에서):
    python -c "from importlib import import_module as m; m('class.thorax.answers').build_json(r'<problems.csv>')"
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
    """사진 이름으로 정답을 찾는다. {"finding", "view", "boxes"} 또는 None.
    st_disease 를 주면 표의 정답 소견과 같을 때만 돌려준다(관리자가 병명을 바꾼 문제를 막는다)."""
    item = _table().get(image_key(st_image))
    if not item:
        return None
    if st_disease is not None and (st_disease or "").strip() != item.get("finding"):
        return None
    if not _valid_boxes(item.get("boxes")):
        return None
    view = item.get("view")
    return {"finding": item.get("finding"), "view": view if view in VIEWS else None,
            "boxes": [dict(box) for box in item["boxes"]]}


def build_json(csv_path: str | Path, out_path: str | Path = ANSWER_FILE) -> dict:
    """problems.csv → answer_boxes.json. 넣은 수·뺀 수를 돌려준다."""
    answers, skipped = {}, {"검토 메모(제외·확인 필요)": 0, "박스 형식 오류": 0}
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
            view = (row.get("view_position") or "").strip().upper()
            answers[row["image_file"].strip()] = {
                "finding": (row.get("st_disease") or "").strip(),
                "view": view if view in VIEWS else None,
                "boxes": boxes,
            }
    data = {"source": SOURCE, "built_from": Path(csv_path).name, "count": len(answers), "answers": answers}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    _table.cache_clear()
    return {"넣음": len(answers), **skipped}
