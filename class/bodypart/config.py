"""
부위 자동 판별 설정.

환경 변수로 덮어쓸 수 있다. 루트 config.py(DB·NAS)와 섞지 않는다.

    BODYPART_WEIGHTS_PATH=C:/models/bodypart/classifier.joblib   (판별 모델 파일)
    BODYPART_MIN_CONFIDENCE=0.8                                  (이 이상 확신할 때만 경고)

BODYPART_WEIGHTS_PATH 가 비어 있으면 모델을 불러오지 않고 항상 "알 수 없음"을 돌려준다.
그래서 모델이 준비되기 전에는 화면에 경고가 뜨지 않는다.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def _env(key: str) -> str:
    """환경 변수 → 프로젝트 .env 순으로 값을 읽는다."""
    value = (os.environ.get(key) or "").strip()
    if value:
        return value
    env_path = BASE_DIR / ".env"
    if env_path.is_file():
        try:
            from dotenv import dotenv_values

            return (dotenv_values(env_path).get(key) or "").strip()
        except Exception:
            pass
    return ""


WEIGHTS_PATH = _env("BODYPART_WEIGHTS_PATH")
try:
    MIN_CONFIDENCE = float(_env("BODYPART_MIN_CONFIDENCE") or "0.8")
except ValueError:
    MIN_CONFIDENCE = 0.8

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))


def model_configured() -> bool:
    """판별 모델 파일 위치가 정해졌는지."""
    return bool(WEIGHTS_PATH)
