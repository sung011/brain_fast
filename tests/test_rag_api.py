"""GET /glossary/search. 색인 파일은 열지 않고, 응답 칸이 /glossary 와 같은지 본다."""
import sys
from importlib import import_module
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers.rag import router  # noqa: E402
from services import ragServices  # noqa: E402

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def test_search_returns_glossary_item_fields(monkeypatch):
    rag = import_module("class.rag")

    def fake_search(question):
        assert question == "허리통증이 있어요"
        return {
            "terms": ["허리통증"],
            "glossary": [],
            "diseases": [
                {
                    "name": "요통",
                    "explanation": "허리 쪽이 아픈 상태이다.",
                    "matched_count": 1,
                    "exact": True,
                    "starts": True,
                    "in_name": 1,
                }
            ],
            "disease_count": 1,
        }

    monkeypatch.setattr(rag, "search", fake_search)
    response = client.get("/glossary/search", params={"q": "허리통증이 있어요"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["q"] == "허리통증이 있어요"
    assert body["terms"] == ["허리통증"]
    assert body["count"] == 1
    assert body["items"] == [
        {
            "id": None,
            "term_id": None,
            "name_ko": "요통",
            "name_en": None,
            "definition_ko": "허리 쪽이 아픈 상태이다.",
            "definition_en": None,
            "body_part": None,
            "tags": None,
            "tags_ko": None,
            "modality": None,
            "category": "disease",
        }
    ]


def test_filler_only_does_not_open_the_index(monkeypatch):
    rag = import_module("class.rag")

    def explode(question):
        raise AssertionError("색인을 열면 안 된다")

    monkeypatch.setattr(rag, "search", explode)
    response = client.get("/glossary/search", params={"q": "있어요"})
    assert response.status_code == 422


def test_missing_query_returns_index_entries(monkeypatch):
    rag = import_module("class.rag")

    def fake_list(*args, **kwargs):
        assert kwargs.get("limit") == ragServices.GLOSSARY_RESULT_LIMIT
        return {
            "terms": [],
            "glossary": [{"name": "두통", "explanation": "머리 부위가 아픈 증상이다."}],
            "diseases": [{"name": "편두통", "explanation": "한쪽 머리가 아픈 병이다."}],
            "disease_count": 1,
        }

    def explode(question):
        raise AssertionError("빈 질문인데 검색하면 안 된다")

    monkeypatch.setattr(rag, "list_entries", fake_list)
    monkeypatch.setattr(rag, "search", explode)
    response = client.get("/glossary/search")
    assert response.status_code == 200
    body = response.json()
    assert body["q"] == ""
    assert body["terms"] == []
    assert body["count"] == 2
    assert [item["name_ko"] for item in body["items"]] == ["두통", "편두통"]
    assert [item["category"] for item in body["items"]] == ["term", "disease"]


def test_missing_index_is_503(monkeypatch):
    rag = import_module("class.rag")

    def missing(question):
        raise rag.RagIndexError("RAG 색인이 없습니다.")

    monkeypatch.setattr(rag, "search", missing)
    response = client.get("/glossary/search", params={"q": "두통"})
    assert response.status_code == 503
    assert response.json()["detail"] == "RAG 색인이 없습니다."


def test_terms_come_before_diseases():
    found = {
        "terms": ["두통"],
        "glossary": [{"name": "두통", "explanation": "머리 부위가 아픈 증상이다."}],
        "diseases": [{"name": "편두통", "explanation": "한쪽 머리가 아픈 병이다."}],
    }
    rag = import_module("class.rag")
    original = rag.search
    rag.search = lambda question: found
    try:
        result = ragServices.search_glossary("두통")
    finally:
        rag.search = original
    assert [item["category"] for item in result["items"]] == ["term", "disease"]
    assert result["items"][0]["name_ko"] == "두통"


def test_search_returns_every_match(monkeypatch):
    rag = import_module("class.rag")
    many = [{"name": f"용어{i}", "explanation": "설명이다 충분히 긴 문장."} for i in range(5)]
    monkeypatch.setattr(ragServices, "GLOSSARY_RESULT_LIMIT", 2)
    monkeypatch.setattr(
        rag,
        "search",
        lambda question: {"terms": ["용어"], "glossary": many, "diseases": [], "disease_count": 0},
    )
    result = ragServices.search_glossary("용어")
    assert result["count"] == 5
    assert [item["name_ko"] for item in result["items"]] == [f"용어{i}" for i in range(5)]
