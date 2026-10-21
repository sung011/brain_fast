"""
뇌 MRI 5질환 분석 설정.

가중치(best.pt)는 저장소에 넣지 않는다. 경로는 환경 변수로 지정한다.
환경 변수 → 프로젝트 .env 순으로 읽는다. 루트 config.py 의 DB·NAS 설정과 섞지 않는다.

- BRAIN_MRI_MODEL_PATH       : YOLO26s-Seg 가중치 파일 경로 (비어 있으면 MRI 분석만 503)
- BRAIN_MRI_CONF_THRESHOLD   : 검출 최소 confidence (기본 0.25)
- BRAIN_MRI_MAX_UPLOAD_BYTES : 업로드 최대 크기 (기본 20MB, DICOM 고려)
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

MODEL_NAME = "YOLO26s-Seg"
MODEL_LABEL = "YOLO26s-Seg brain MRI 5-disease"
