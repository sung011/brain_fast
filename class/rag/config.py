"""
의학용어 검색(class/rag) 설정.

색인 폴더와 임베딩 모델 이름만 읽는다. DB 설정은 루트 config.py 에 둔다.
값은 환경 변수 → 프로젝트 .env → 기본값 순서로, 호출할 때마다 읽는다.

  RAG_FAISS_DIR      FAISS 색인 폴더. 비어 있으면 assets/data/faiss_02
  RAG_EMBED_MODEL    색인을 만들 때 쓴 Ollama 임베딩 모델 이름
  RAG_OLLAMA_BASE_URL  (선택) Ollama 주소. 비어 있으면 라이브러리 기본 주소를 쓴다
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"
DEFAULT_FAISS_DIR = BASE_DIR / "assets" / "data" / "faiss_02"
DEFAULT_EMBED_MODEL = "qwen3-embedding:8b"


def _read_dotenv(key: str) -> str:
    try:
        text = ENV_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip("\"'")
    return ""


def get_setting(key: str, default: str = "") -> str:
    value = (os.environ.get(key) or "").strip()
    return value or _read_dotenv(key) or default


def faiss_dir() -> Path:
    raw = get_setting("RAG_FAISS_DIR")
    if not raw:
        return DEFAULT_FAISS_DIR
    path = Path(raw)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def embed_model() -> str:
    return get_setting("RAG_EMBED_MODEL", DEFAULT_EMBED_MODEL) or DEFAULT_EMBED_MODEL


def ollama_base_url() -> str:
    return get_setting("RAG_OLLAMA_BASE_URL")
