"""
무릎 MRI 분석(이상·ACL·반월판 + ACL 위치) 설정.

모델 가중치는 깃에 올리지 않고 Hugging Face 에서 내려받는다.
설정값은 "환경 변수 → 프로젝트 .env → 기본값" 순서로 찾고, 호출할 때마다 읽는다.
(import 시점에 값을 굳히지 않으므로 .env 를 나중에 바꾸거나 테스트에서 바꿔도 반영된다.)

  KNEE_HF_REPO       가중치 저장소. 예: 내계정/Knee_MRI (각자 계정 사용)
  KNEE_HF_REVISION   (선택) 검토한 커밋 해시. 적으면 그 버전만 내려받아 결과가 섞이지 않는다.
  LOAD_KNEE_ON_STARTUP  1 이면 서버 시작 때 미리 로드
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"


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


def hf_repo() -> str:
    return get_setting("KNEE_HF_REPO")


def hf_revision() -> str | None:
    return get_setting("KNEE_HF_REVISION") or None


def load_on_startup() -> bool:
    return get_setting("LOAD_KNEE_ON_STARTUP", "0") == "1"


# 소견(task) × 촬영면(plane) 별 체크포인트. 같은 면에 여러 개면 확률을 평균낸다.
PLANES = ("axial", "coronal", "sagittal")
TASKS = ("abnormal", "acl", "meniscus")

_MENISCUS_SEEDS = ("s0", "s7", "s123", "s2024")

# 반월판은 결합기(combiner.json)가 axial·coronal 만 쓰도록 골랐으므로 sagittal 모델은 올리지 않았다.
CKPT_FILES: dict[tuple[str, str], list[str]] = {
    ("abnormal", "axial"): ["best_abnormal_axial.pt"],
    ("abnormal", "coronal"): ["best_abnormal_coronal.pt"],
    ("abnormal", "sagittal"): ["best_abnormal_sagittal.pt"],
    ("acl", "axial"): ["best_acl_axial.pt"],
    ("acl", "coronal"): ["best_acl_coronal.pt"],
    ("acl", "sagittal"): ["best_acl_combined.pt"],
    ("meniscus", "axial"): [f"best_meniscus_axial_{s}.pt" for s in _MENISCUS_SEEDS],
    ("meniscus", "coronal"): [f"best_meniscus_coronal_{s}.pt" for s in _MENISCUS_SEEDS],
}

# 소견별로 어떤 면을 합쳐서 판단할지, 확률을 어떻게 보정할지 적은 파일
COMBINER_FILE = "combiner.json"
CALIBRATION_FILE = "calibration.json"

# ACL 이 있는 대략적 해부학 위치 안내용 YOLO 모델 (병변 위치가 아님)
ACL_YOLO_FILE = "acl_yolo.pt"

# 학습 때 224 로 학습했다. 바꾸면 결과가 달라지므로 환경 변수로 열어두지 않는다.
IMG_SIZE = 224

# 입력 크기 한도 (메모리·지연 보호). 운영 측정 후 조정한다.
MAX_SLICES = 128
MAX_SIDE = 1024
MAX_VOXELS = 16_000_000

# 보정된 확률이 이 구간 안이면 "판단 어려움"으로 표시한다. (반올림 전 값으로 판정)
GATING_BAND = {
    "abnormal": (0.40, 0.60),
    "acl": (0.40, 0.60),
    "meniscus": (0.40, 0.60),
}
