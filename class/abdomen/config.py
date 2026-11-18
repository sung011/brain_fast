"""
복부 CT 장기 검출 분석 설정.

환경 변수 → 프로젝트 .env 순으로 읽는다. 루트 config.py 의 DB·NAS 설정과 섞지 않는다.

- ABDOMEN_HF_REPO          : 가중치를 받을 Hugging Face 저장소 (예: kimsungil/abdomen_m1)
- ABDOMEN_HF_FILENAME      : 그 저장소의 가중치 파일 이름 (기본 best.pt)
- ABDOMEN_HF_REVISION      : (선택) 받을 커밋
- ABDOMEN_MODEL_PATH       : 로컬 가중치 파일이 있으면 그 파일을 쓰고 HF 는 받지 않는다
- ABDOMEN_CONF_THRESHOLD   : 검출 최소 confidence (기본 0.15, 학습 README 와 같음)
- ABDOMEN_IMG_SIZE         : YOLO 입력 크기 (기본 640)
- ABDOMEN_MAX_UPLOAD_BYTES : 업로드 최대 크기 (기본 5MB)
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def _env(key: str, default: str = "") -> str:
    """환경 변수 → 프로젝트 .env 순으로 값을 찾는다."""
    value = (os.environ.get(key) or "").strip()
    if value:
        return value
    env_path = BASE_DIR / ".env"
    if env_path.is_file():
        try:
            from dotenv import dotenv_values

            value = (dotenv_values(env_path).get(key) or "").strip()
            if value:
                return value
        except Exception:
            pass
    return default


def _resolve_hf_token() -> str:
    for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    env_path = BASE_DIR / ".env"
    if env_path.is_file():
        try:
            from dotenv import dotenv_values

            values = dotenv_values(env_path)
            for key in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
                value = (values.get(key) or "").strip()
                if value:
                    return value
        except Exception:
            pass
    return ""


HF_TOKEN = _resolve_hf_token()
MODEL_PATH = _env("ABDOMEN_MODEL_PATH")
CONF_THRESHOLD = float(_env("ABDOMEN_CONF_THRESHOLD", "0.15"))
IMG_SIZE = int(_env("ABDOMEN_IMG_SIZE", "640"))
MAX_UPLOAD_BYTES = int(_env("ABDOMEN_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
HF_FILENAME_DEFAULT = "best.pt"

MEDGEMMA_MODEL_ID = _env("MEDGEMMA_MODEL_ID", "google/medgemma-1.5-4b-it") or "google/medgemma-1.5-4b-it"
MEDGEMMA_MAX_NEW_TOKENS = int(_env("MEDGEMMA_MAX_NEW_TOKENS", "768") or "768")
MEDGEMMA_DUAL_IMAGE = (_env("MEDGEMMA_DUAL_IMAGE", "1") or "1") == "1"
MEDGEMMA_BACKEND = (_env("MEDGEMMA_BACKEND", "template") or "template").strip().lower()
if MEDGEMMA_BACKEND not in ("api", "local", "template"):
    MEDGEMMA_BACKEND = "template"
MEDGEMMA_FALLBACK_TEMPLATE = (_env("MEDGEMMA_FALLBACK_TEMPLATE", "1") or "1") == "1"

MODEL_NAME = "YOLO26m"
MODEL_LABEL = "YOLO26m abdomen CT organ detection"


def hf_repo() -> str:
    """가중치를 받을 Hugging Face 저장소. 비어 있으면 받지 않는다(뇌 MRI 와 같음)."""
    return _env("ABDOMEN_HF_REPO")


def hf_filename() -> str:
    return _env("ABDOMEN_HF_FILENAME", HF_FILENAME_DEFAULT) or HF_FILENAME_DEFAULT


def hf_revision() -> str | None:
    return _env("ABDOMEN_HF_REVISION") or None


def model_configured() -> bool:
    """로컬 가중치 경로 또는 Hugging Face 저장소가 있으면 분석을 시도할 수 있다.
    둘 다 비어 있으면 복부 분석·채점만 503 이고 다른 기능은 그대로다."""
    return bool(MODEL_PATH or hf_repo())
