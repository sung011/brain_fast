"""의학용어 검색: 질문 해석, 같은 병명 한 번만, 벡터 조각 걸러 내기. 색인 파일은 열지 않는다."""
import sys
from importlib import import_module
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

query = import_module("class.rag.query")
entries = import_module("class.rag.entries")
retrieve = import_module("class.rag.retrieve")
store = import_module("class.rag.store")


def test_fillers_and_josa_do_not_become_terms():
    assert query.query_terms("두통과 구토가 있고 허리통증이 있어요") == ["두통", "구토", "허리통증"]


def test_single_hangul_stays_only_when_it_is_the_whole_question():
    assert query.query_terms("뇌") == ["뇌"]
    assert query.query_terms("있어요") == []


def test_comma_splits_terms():
    assert query.query_terms("두통, 구토") == ["두통", "구토"]


def test_back_pain_synonym_matches_disease_name_once():
    chunks = {
        1: ["kr: 요통, 허리통증\ndefinition_kr: 허리 쪽이 아픈 상태를 말한다."],
        2: ["kr: 허리통증, 요통\ndefinition_kr: 같은 이름을 순서만 바꾼 항목이다."],
        3: ["kr: 두통\ndefinition_kr: 머리 부위가 아픈 증상이다."],
    }
    terms, glossary, diseases, total = retrieve.find_answers("허리통증이 있어요", chunks=chunks)
    assert terms == ["허리통증"]
    assert total == 1
    assert [item[4] for item in diseases] == ["요통, 허리통증"]
    assert glossary == []


def test_exact_non_disease_goes_to_glossary_once():
    chunks = {
        1: ["kr: 두통\ndefinition_kr: 머리 부위가 아픈 증상이다."],
        2: ["kr: 두통\ndefinition_kr: 같은 이름의 두 번째 행이다."],
    }
    terms, glossary, diseases, _ = retrieve.find_answers("두통", chunks=chunks)
    assert terms == ["두통"]
    assert len(glossary) == 1
    assert glossary[0][4] == "두통"
    assert diseases == []


def test_entry_keeps_english_name_and_definition():
    chunks = {
        1: [
            "en: headache\n"
            "kr: 두통\n"
            "definition: Pain in the head. It can follow illness.\n"
            "definition_kr: 머리 부위가 아픈 증상이다."
        ],
    }
    row = retrieve.list_entries(chunks)["glossary"][0]
    assert row["name"] == "두통"
    assert row["name_en"] == "headache"
    assert "머리" in row["explanation"]
    assert row["definition_en"] == "Pain in the head. It can follow illness."


def test_short_english_does_not_cut_inside_a_sentence():
    text = (
        "The part of CENTRAL NERVOUS SYSTEM that is contained within the skull (CRANIUM). "
        "Arising from the NEURAL TUBE, the embryonic brain is comprised of three major parts "
        "including PROSENCEPHALON (the forebrain); MESENCEPHALON (the midbrain); "
        "and RHOMBENCEPHALON (the hindbrain). "
        "The developed brain consists of CEREBRUM."
    )
    shortened = entries.short_english(text)
    assert shortened.startswith("The part of")
    assert "(the." not in shortened
    assert shortened.endswith("CEREBRUM.")


def test_list_entries_keeps_terms_and_diseases_without_a_question():
    chunks = {
        1: ["kr: 두통\ndefinition_kr: 머리 부위가 아픈 증상이다."],
        2: ["kr: 폐렴\ndefinition_kr: 폐에 염증이 생긴 병이다."],
        3: ["kr: 폐렴\ndefinition_kr: 같은 이름의 두 번째 행이다."],
    }
    found = retrieve.list_entries(chunks)
    assert found["terms"] == []
    assert [row["name"] for row in found["glossary"]] == ["두통"]
    assert [row["name"] for row in found["diseases"]] == ["폐렴"]


def test_list_entries_limit_picks_that_many(monkeypatch):
    chunks = {
        i: [f"kr: 용어{i}\ndefinition_kr: 이것은 충분히 긴 설명 문장이다."]
        for i in range(5)
    }
    monkeypatch.setattr(retrieve.random, "sample", lambda pool, k: pool[:k])
    found = retrieve.list_entries(chunks, limit=2)
    assert len(found["glossary"]) + len(found["diseases"]) == 2


def test_plain_answer_lists_terms_and_says_when_no_disease():
    chunks = {
        1: ["kr: 두통\ndefinition_kr: 머리 부위가 아픈 증상이다."],
    }
    text = retrieve.plain_answer("두통이 있어요", chunks=chunks)
    assert text.startswith("검색어: 두통")
    assert "용어:" in text
    assert "관련 병: 자료에 없음" in text


def test_short_explanation_drops_english_parentheses():
    text = entries.short_explanation("머리가 아프다(headache). 쉬면 나아진다.")
    assert "headache" not in text
    assert "머리가 아프다" in text


def test_region_name_is_not_a_disease():
    assert entries.is_disease("가슴") is False
    assert entries.is_disease("폐렴") is True


def test_vector_hits_skip_short_hangul_and_duplicate_rows(monkeypatch):
    class Doc:
        def __init__(self, content, row):
            self.page_content = content
            self.metadata = {"row": row}

    def fake_search(question, fetch_k):
        assert fetch_k == 80
        return [
            (Doc("B.", 1), 0.9),
            (Doc("요통이 심한 환자 설명입니다", 2), 0.4),
            (Doc("같은 행의 두번째 조각입니다", 2), 0.7),
            (Doc("머리만 아픈 다른 증상 설명", 3), 0.8),
        ]

    monkeypatch.setattr(store, "similarity_search", fake_search)
    monkeypatch.setattr(
        store,
        "entry_from_row",
        lambda row: {2: ("요통", "허리통증이 있다."), 3: ("두통", "머리가 아프다.")}.get(row, (None, None)),
    )

    hits = retrieve.vector_hits("허리통증이 있어요")
    assert len(hits) == 1
    score, name, explanation = hits[0]
    assert name == "요통"
    assert explanation == "허리통증이 있다."
    assert score == 0.4


def test_missing_index_raises(monkeypatch):
    store.reset()
    monkeypatch.setenv("RAG_FAISS_DIR", "/tmp/brain_fast_rag_missing_index")
    with pytest.raises(store.RagIndexError):
        store.row_chunks()
    store.reset()
