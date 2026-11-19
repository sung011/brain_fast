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
