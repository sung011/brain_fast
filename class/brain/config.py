"""
뇌 출혈 분석(화면 공유 ROI) 설정.

환경 변수로 덮어쓸 수 있다.
HF_TOKEN 이 없으면 앙상블·MedGemma 다운로드가 실패할 수 있다.
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


HF_TOKEN = _resolve_hf_token()
if HF_TOKEN:
    os.environ["HF_TOKEN"] = HF_TOKEN
    os.environ["HUGGING_FACE_HUB_TOKEN"] = HF_TOKEN

HF_REPO = os.environ.get("HF_REPO", "kimsungil/Brain_Hemorrhage")
# 출혈(ICH) 6클래스 앙상블
CKPT_FILES = [
    "best_m0_convnext_base.pt",
    "best_m0_effv2_m.pt",
]
# germinoma CT 이진(tumor) 앙상블 — 분석 시 출혈과 함께 추론
GERMINOMA_CKPT_FILES = [
    "best_germinoma_ct_convnext_base.pt",
    "best_germinoma_ct_effv2_m.pt",
]
ENABLE_GERMINOMA = os.environ.get("ENABLE_GERMINOMA", "1") == "1"

IMG_SIZE = int(os.environ.get("IMG_SIZE", "384"))
WINDOW_MODE = os.environ.get("WINDOW_MODE", "brain_subdural")
USE_TTA_FLIP = os.environ.get("USE_TTA_FLIP", "1") == "1"
ANY_MODE = os.environ.get("ANY_MODE", "model")
HIGH_PROB_THR = float(os.environ.get("HIGH_PROB_THR", "0.9"))

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))

MEDGEMMA_MODEL_ID = os.environ.get("MEDGEMMA_MODEL_ID", "google/medgemma-1.5-4b-it")
MEDGEMMA_MAX_NEW_TOKENS = int(os.environ.get("MEDGEMMA_MAX_NEW_TOKENS", "768"))
MEDGEMMA_DUAL_IMAGE = os.environ.get("MEDGEMMA_DUAL_IMAGE", "1") == "1"
# api = HF Inference API(원격) / template = 앙상블 요약(다운로드·API 없음) / local = PC 8GB+
MEDGEMMA_BACKEND = os.environ.get("MEDGEMMA_BACKEND", "api").strip().lower()
if MEDGEMMA_BACKEND not in ("api", "local", "template"):
    MEDGEMMA_BACKEND = "api"
# api 실패(403 등) 시 template 으로 자동 대체. 0 이면 오류 그대로 반환.
MEDGEMMA_FALLBACK_TEMPLATE = os.environ.get("MEDGEMMA_FALLBACK_TEMPLATE", "1") == "1"
LOAD_MEDGEMMA_ON_STARTUP = os.environ.get("LOAD_MEDGEMMA_ON_STARTUP", "0") == "1"

# 관리자 서버가 바로 뜨도록 기본은 끄고, 첫 분석 요청에서 로드한다.
LOAD_MODELS_ON_STARTUP = os.environ.get("LOAD_MODELS_ON_STARTUP", "0") == "1"
