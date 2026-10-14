"""
흉부 X-ray 분석 설정.

환경 변수로 덮어쓸 수 있다. 루트 config.py(DB·NAS)와 섞지 않는다.
THORAX_HF_REPO 와 THORAX_CKPT_FILES 가 비어 있으면 모델을 불러오지 않고
"흉부 모델 준비 중" 오류를 돌려준다.
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


def _env_list(key: str) -> list[str]:
    raw = os.environ.get(key, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


HF_TOKEN = _resolve_hf_token()

# 예) THORAX_HF_REPO=team/Chest_Xray
#     THORAX_CKPT_FILES=best_cxr_convnext_base.pt,best_cxr_effv2_m.pt
HF_REPO = os.environ.get("THORAX_HF_REPO", "").strip()
CKPT_FILES = _env_list("THORAX_CKPT_FILES")

IMG_SIZE = int(os.environ.get("THORAX_IMG_SIZE", "384"))
USE_TTA_FLIP = os.environ.get("THORAX_USE_TTA_FLIP", "0") == "1"
# 이 확률 이상인 소견을 모두 위치 표시한다. 없으면 최상위 1개만 표시한다.
HIGH_PROB_THR = float(os.environ.get("THORAX_HIGH_PROB_THR", "0.5"))

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))


def model_configured() -> bool:
    """가중치 위치가 정해졌는지."""
    return bool(HF_REPO and CKPT_FILES)
