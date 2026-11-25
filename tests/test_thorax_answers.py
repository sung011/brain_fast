"""흉부 의사 정답 박스 표(class/thorax/answer_boxes.json) 시험. DB·모델을 쓰지 않는다.

- 사진 이름으로 찾기(관리자 업로드 접두어 떼기), 병명이 다르면 None, 표에 없으면 None
- 흉부가 아닌 문제는 None (뇌·무릎 채점은 그대로)
- 표의 박스가 모두 0~1 범위이고 AP/PA 가 있다
"""
import json
from importlib import import_module
from types import SimpleNamespace

import pytest

from services import learningServices

answers = import_module("class.thorax.answers")
TABLE = json.loads(answers.ANSWER_FILE.read_text(encoding="utf-8"))["answers"]
NAME, ITEM = next(iter(TABLE.items()))


def test_image_key_strips_upload_prefix():
    assert answers.image_key("/a/b/0123456789_00000032_037.png") == "00000032_037.png"
    assert answers.image_key('["/a/0123456789_x.png", "/a/y.png"]') == "x.png"
    assert answers.image_key("/a/00000032_037.png") == "00000032_037.png"
    assert answers.image_key(None) == ""


def test_table_is_complete_and_in_range():
    assert len(TABLE) == 401
    for item in TABLE.values():
        assert item["finding"] in {"폐음영", "흉막삼출", "기흉", "심비대"}
        assert item["view"] in {"AP", "PA"}
        assert answers._valid_boxes(item["boxes"])


def test_find_answer_by_uploaded_path():
    found = answers.find_answer(f"/web/chest/abcdef0123_{NAME}", ITEM["finding"])
    assert found["boxes"] == ITEM["boxes"] and found["view"] == ITEM["view"]


def test_find_answer_rejects_other_disease_and_unknown_image():
    other = "기흉" if ITEM["finding"] != "기흉" else "폐음영"
    assert answers.find_answer(f"/x/{NAME}", other) is None
    assert answers.find_answer("/x/not_in_table.png", ITEM["finding"]) is None


@pytest.fixture()
def fake_study(monkeypatch):
    rows = {
        1: SimpleNamespace(st_part="2", st_image=f"/x/0123456789_{NAME}", st_disease=ITEM["finding"]),
        2: SimpleNamespace(st_part="1", st_image=f"/x/0123456789_{NAME}", st_disease=ITEM["finding"]),  # 뇌
        3: SimpleNamespace(st_part="2", st_image="/x/0123456789_unknown.png", st_disease="기흉"),
    }
    monkeypatch.setattr(learningServices.study_repositories, "get_by_idx", lambda db, idx: rows.get(idx))


def test_get_answer_boxes_only_for_thorax_in_table(fake_study):
    assert learningServices.get_answer_boxes(None, 1) == ITEM["boxes"]
    assert learningServices.get_view_position(None, 1) == ITEM["view"]
    assert learningServices.get_answer_boxes(None, 2) is None
    assert learningServices.get_answer_boxes(None, 3) is None
    assert learningServices.get_answer_boxes(None, 99) is None
    assert learningServices.get_answer_boxes(None, None) is None


ALSO_NAME, ALSO_ITEM = next((k, v) for k, v in TABLE.items() if v.get("also"))


def test_table_has_co_existing_findings_from_nih_labels():
    """NIH 원래 라벨에 같이 적힌 4소견이 also 로 들어 있다(대표 소견은 빠짐, 박스는 없음)."""
    assert sum(1 for v in TABLE.values() if v.get("also")) == 144
    for v in TABLE.values():
        assert v["finding"] not in v.get("also", [])
        assert set(v.get("also", [])) <= {"폐음영", "흉막삼출", "기흉", "심비대"}
    found = answers.find_answer(f"/x/{ALSO_NAME}", ALSO_ITEM["finding"])
    assert found["also"] == ALSO_ITEM["also"] and found["also_boxes"] == []


def test_build_json_reads_also_column_and_splits_boxes(tmp_path):
    """ChestX-Det 문제처럼 also_findings 열과 소견별 박스가 있으면, 대표 소견 박스와 같이 있는 소견 박스를 나눈다."""
    csv_path = tmp_path / "problems.csv"
    boxes = [{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2, "finding": "기흉"},
             {"x": 0.5, "y": 0.5, "width": 0.3, "height": 0.3, "finding": "흉막삼출"}]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        import csv as _csv
        w = _csv.writer(f)
        w.writerow(["image_file", "st_disease", "also_findings", "answer_boxes", "review_note"])
        w.writerow(["cxd_1.png", "기흉", "흉막삼출|모르는소견", json.dumps(boxes, ensure_ascii=False), ""])
    out = tmp_path / "answer_boxes.json"
    assert answers.build_json([csv_path], out)["넣음"] == 1
    item = json.loads(out.read_text(encoding="utf-8"))["answers"]["cxd_1.png"]
    assert item["also"] == ["흉막삼출"] and item["view"] is None
    answers._table.cache_clear()
    monkey = answers.ANSWER_FILE
    try:
        answers.ANSWER_FILE = out
        found = answers.find_answer("/x/0123456789_cxd_1.png", "기흉")
        assert [b["finding"] for b in found["boxes"]] == ["기흉"]
        assert [b["finding"] for b in found["also_boxes"]] == ["흉막삼출"]
    finally:
        answers.ANSWER_FILE = monkey
        answers._table.cache_clear()


def test_submit_tells_when_answer_is_a_co_existing_finding(monkeypatch):
    """병명을 사진에 같이 있는 다른 소견으로 내면 오답은 그대로, also_match 로 알려 준다."""
    row = SimpleNamespace(st_part="2", st_image=f"/x/0123456789_{ALSO_NAME}", st_disease=ALSO_ITEM["finding"],
                          del_yn="N", state="N", st_modal="1")
    monkeypatch.setattr(learningServices.study_repositories, "get_by_idx", lambda db, idx: row)
    monkeypatch.setattr(learningServices, "_merge_exclude_idxs", lambda db, **kw: [])
    monkeypatch.setattr(learningServices, "get_random_problem", lambda db, **kw: None)
    other = ALSO_ITEM["also"][0]
    res = learningServices.submit_problem(None, study_idx=1, answer=other)
    assert res["correct"] is False and res["also_match"] is True and res["also_findings"] == ALSO_ITEM["also"]
    res = learningServices.submit_problem(None, study_idx=1, answer=ALSO_ITEM["finding"])
    assert res["correct"] is True and res["also_match"] is False
    assert learningServices.get_answer_also(None, 1) == {"findings": ALSO_ITEM["also"], "boxes": []}
