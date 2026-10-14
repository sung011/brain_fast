"""
흉부 X-ray 분석 설정.

환경 변수로 덮어쓸 수 있다. 루트 config.py(DB·NAS)와 섞지 않는다.

모델은 소견마다 "패키지" 폴더 하나로 둔다. 패키지 안에는
best.pt(가중치)와 inference_config.json(입력 크기·기준값·소견 이름)이 있다.

    THORAX_HF_REPO=team/MediScan_Chest
    THORAX_PACKAGES=mediscan_opacity_detector_v2,mediscan_effusion_detector_v2_1

Hugging Face 저장소 안에 패키지 이름의 폴더가 있어야 한다.
개발 중 HF에 올리기 전에는 THORAX_LOCAL_DIR 로 PC 폴더에서 읽을 수 있다(운영에서는 비워 둔다).
둘 다 비어 있으면 모델을 불러오지 않고 "흉부 모델 준비 중"을 돌려준다.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def _resolve_hf_token() -> str:
    """환경 변수 → 프로젝트 .env 순으로 Hugging Face 토큰을 찾는다."""
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


HF_TOKEN = _resolve_hf_token()
HF_REPO = _env("THORAX_HF_REPO")
PACKAGES = [p.strip() for p in _env("THORAX_PACKAGES").split(",") if p.strip()]
LOCAL_DIR = _env("THORAX_LOCAL_DIR")

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))


def model_configured() -> bool:
    """모델 패키지 위치가 정해졌는지."""
    return bool(PACKAGES and (HF_REPO or LOCAL_DIR))
