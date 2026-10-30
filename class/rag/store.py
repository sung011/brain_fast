"""
로컬 색인에서 용어 글을 읽는다.

노트북의 plain_answer 와 같이 질문 검색은 index.pkl 의 용어 글만 본다.
1GB가 넘는 벡터 파일과 Ollama 임베딩은 벡터 검색(vector_hits)에서만 연다.
이 서버에 있는 색인 폴더만 읽고, 요청으로 받은 파일은 열지 않는다.
"""
import pickle
from collections import defaultdict

from . import config
from .entries import entry_from_parts


class RagIndexError(Exception):
    """색인 파일을 열지 못했다. 질문 해석 오류가 아니다."""


_chunks: dict | None = None
_vec_db = None

_DOC_CLASS = "langchain_core.documents.base.Document"
_STORE_CLASS = "langchain_community.docstore.in_memory.InMemoryDocstore"


class _PickledDocument:
    """색인에 들어 있는 문서. 외부 라이브러리 없이 글과 행 번호만 꺼낸다."""

    def __setstate__(self, state):
        inner = state.get("__dict__") if isinstance(state, dict) else None
        if not isinstance(inner, dict):
            inner = {}
        self.page_content = inner.get("page_content") or ""
        self.metadata = inner.get("metadata") or {}


class _PickledDocstore:
    def __setstate__(self, state):
        raw = state.get("_dict") if isinstance(state, dict) else None
        self._dict = raw if isinstance(raw, dict) else {}


class _IndexUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        key = f"{module}.{name}"
        if key == _DOC_CLASS:
            return _PickledDocument
        if key == _STORE_CLASS:
            return _PickledDocstore
        if module == "builtins":
            return super().find_class(module, name)
        raise RagIndexError("RAG 색인에서 문서 목록을 읽지 못했습니다.")


def reset() -> None:
    """불러 둔 색인을 잊는다. 테스트에서 경로를 바꾼 뒤에 쓴다."""
    global _chunks, _vec_db
    _chunks = None
    _vec_db = None


def row_chunks() -> dict:
    _ensure_chunks()
    return _chunks or {}


def entry_from_row(row_i):
    parts = row_chunks().get(row_i)
    if not parts:
        return None, None
    return entry_from_parts(parts)


def similarity_search(question: str, fetch_k: int):
    _ensure_vectors()
    return _vec_db.similarity_search_with_relevance_scores(question, k=fetch_k)


def _index_dir():
    path = config.faiss_dir()
    if not (path / "index.pkl").is_file():
        raise RagIndexError(f"RAG 색인이 없습니다: {path}")
    return path


def _ensure_chunks() -> None:
    global _chunks
    if _chunks is not None:
        return

    path = _index_dir()
    try:
        with (path / "index.pkl").open("rb") as handle:
            loaded = _IndexUnpickler(handle).load()
    except RagIndexError:
        raise
    except Exception as exc:
        raise RagIndexError("RAG 색인에서 문서 목록을 읽지 못했습니다.") from exc

    docstore = loaded[0] if isinstance(loaded, tuple) and loaded else loaded
    raw = getattr(docstore, "_dict", None)
    if not isinstance(raw, dict):
        raise RagIndexError("RAG 색인에서 문서 목록을 읽지 못했습니다.")

    grouped: dict = defaultdict(list)
    for doc in raw.values():
        meta = getattr(doc, "metadata", None) or {}
        grouped[meta.get("row")].append(getattr(doc, "page_content", "") or "")
    _chunks = dict(grouped)


def _ensure_vectors() -> None:
    global _vec_db
    if _vec_db is not None:
        return

    path = _index_dir()
    if not (path / "index.faiss").is_file():
        raise RagIndexError(f"RAG 색인이 없습니다: {path}")

    try:
        from langchain_community.vectorstores import FAISS
        from langchain_ollama import OllamaEmbeddings
    except ImportError as exc:
        raise RagIndexError(
            "벡터 검색 라이브러리가 없습니다. langchain-ollama, langchain-community, faiss-cpu 가 필요합니다."
        ) from exc

    kwargs = {"model": config.embed_model()}
    base_url = config.ollama_base_url()
    if base_url:
        kwargs["base_url"] = base_url
    embeddings = OllamaEmbeddings(**kwargs)
    _vec_db = FAISS.load_local(str(path), embeddings, allow_dangerous_deserialization=True)
    _ensure_chunks()
