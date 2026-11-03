"""
뇌 MRI 5질환 분석 설정.

가중치(best.pt)는 저장소에 넣지 않는다. 경로는 환경 변수로 지정한다.
환경 변수 → 프로젝트 .env 순으로 읽는다. 루트 config.py 의 DB·NAS 설정과 섞지 않는다.

- BRAIN_MRI_MODEL_PATH       : YOLO26s-Seg 가중치 파일 경로. 있으면 이 파일을 쓴다.
- BRAIN_MRI_HF_REPO          : 경로가 없을 때 받을 Hugging Face 저장소.
- BRAIN_MRI_HF_FILENAME      : 그 저장소의 가중치 파일 이름 (기본 best.pt)
- BRAIN_MRI_HF_REVISION      : (선택) 받을 커밋
- BRAIN_MRI_CONF_THRESHOLD   : 검출 최소 confidence (기본 0.25)
- BRAIN_MRI_MAX_UPLOAD_BYTES : 업로드 최대 크기 (기본 20MB)
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


MODEL_PATH = _env("BRAIN_MRI_MODEL_PATH")
CONF_THRESHOLD = float(_env("BRAIN_MRI_CONF_THRESHOLD", "0.25"))
MAX_UPLOAD_BYTES = int(_env("BRAIN_MRI_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
HF_FILENAME_DEFAULT = "best.pt"


def hf_repo() -> str:
    """가중치를 받을 Hugging Face 저장소. 로컬 파일이 있으면 다운로드는 하지 않는다."""
    return _env("BRAIN_MRI_HF_REPO")


def hf_filename() -> str:
    return _env("BRAIN_MRI_HF_FILENAME", HF_FILENAME_DEFAULT) or HF_FILENAME_DEFAULT


def hf_revision() -> str | None:
    return _env("BRAIN_MRI_HF_REVISION") or None


def model_configured() -> bool:
    """로컬 가중치 경로 또는 Hugging Face 저장소가 있으면 분석을 시도할 수 있다."""
    return bool(MODEL_PATH or hf_repo())

MODEL_NAME = "YOLO26s-Seg"
MODEL_LABEL = "YOLO26s-Seg brain MRI 5-disease"
