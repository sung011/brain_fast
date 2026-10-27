"""
무릎 MRI 분석(이상·ACL·반월판 + ACL 위치) 설정.

모델 가중치는 깃에 올리지 않고 Hugging Face 에서 내려받는다.
설정값은 "환경 변수 → 프로젝트 .env → 기본값" 순서로 찾고, 호출할 때마다 읽는다.
(import 시점에 값을 굳히지 않으므로 .env 를 나중에 바꾸거나 테스트에서 바꿔도 반영된다.)

  KNEE_HF_REPO       가중치 저장소. 예: 내계정/Knee_MRI (각자 계정 사용)
  KNEE_HF_REVISION   (선택) 검토한 커밋 해시. 적으면 그 버전만 내려받아 결과가 섞이지 않는다.
  KNEE_HF_TOKEN      (선택) 저장소가 비공개일 때만 필요한 읽기 전용 토큰. 이 저장소만 읽을 수 있게 만든 것을 쓴다.
                     뇌 모듈의 HF_TOKEN(다른 계정)과는 별개이며 서로 대신 쓰지 않는다.
                     **비어 있으면 토큰을 아예 보내지 않는다**(다운로드에 token=False 를 넘긴다). 공개 저장소는 토큰 없이 받아지고
                     비공개 저장소는 안전하게 거절된다. (token=None 을 넘기면 huggingface_hub 가 환경 변수 HF_TOKEN 이나
                     로그인 캐시에서 토큰을 알아서 찾아 보내므로 뇌 토큰이 몰래 쓰인다. 그래서 None 은 쓰지 않는다.)
                     값은 로그·오류 문구·traceback 에 남기지 않는다.
  LOAD_KNEE_ON_STARTUP  1 이면 서버 시작 때 미리 로드
"""
import os
import re
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


# 영상 공급원 두 키 전용(MSN-20261016-003 FIVE-06). get_setting 은 빈 환경변수를 .env 값으로 채우므로, 실행 환경에서 KNEE_IMAGE_DIR 를
# 비워도 .env 의 로컬 폴더가 다시 살아나 "NAS 만" 켤 수 없었다. 이 두 키에 한해서는 **환경변수 키가 있으면 빈 값도 그대로**(명시적 비활성화)
# 쓰고, 키가 없을 때만 .env 를 본다. 기본값은 없다(없으면 빈 문자열 = 닫힘). 다른 설정(토큰·루트 등)의 get_setting 계약은 바꾸지 않는다.
SOURCE_KEYS = ("KNEE_IMAGE_DIR", "KNEE_NAS_IMAGE_BASE_URL")


def source_setting(key: str) -> str:
    if key not in SOURCE_KEYS:
        raise ValueError("source_setting 은 영상 공급원 두 키에만 쓴다")
    if key in os.environ:
        return (os.environ.get(key) or "").strip()
    return _read_dotenv(key)


# 데이터 묶음 선택(FIVE-05): local(기본) = 기존 열람 등록부·승인표 데이터 파일, nas_presentation = 발표용 JSON 묶음 하나.
# 그 밖의 값이면 닫힘(어떤 열람·채점도 제공하지 않음). 프로세스가 시작할 때 한 번 읽어 고정한다(실행 중 바꿔도 재시작 전에는 반영 안 됨).
DATA_PROFILES = ("local", "nas_presentation")


def data_profile() -> str:
    return (get_setting("KNEE_DATA_PROFILE", "local") or "local").strip()


def presentation_bundle_path() -> str:
    return get_setting("KNEE_PRESENTATION_BUNDLE", "")


def hf_repo() -> str:
    return get_setting("KNEE_HF_REPO")


def hf_revision() -> str | None:
    return get_setting("KNEE_HF_REVISION") or None


def hf_token() -> str | None:
    """무릎 전용 읽기 토큰. 없으면 None. 뇌용 HF_TOKEN 은 일부러 보지 않는다(다른 계정의 토큰이라 권한이 다르다)."""
    return get_setting("KNEE_HF_TOKEN") or None


def hf_download_token() -> "str | bool":
    """다운로드 경계(앙상블·YOLO)에서 hf_hub_download 의 token= 으로 넘기는 값. 무릎 토큰이 있으면 그 문자열, 없으면 False.

    None 이 아니라 False 인 이유: None 은 '기본 토큰 탐색 허용'이라 huggingface_hub 가 프로세스 HF_TOKEN(뇌 설정이 올리는 값)이나
    로그인 캐시 토큰을 Authorization 에 몰래 싣는다. False 는 '토큰을 보내지 말라'는 뜻이다.
    공용 HF_TOKEN·뇌 설정·전역 환경 변수는 건드리지 않고 무릎 다운로드 두 곳에서만 막는다.
    """
    return hf_token() or False


# 진짜 HF 토큰의 모양(hf_ + 영숫자 20자 이상). 무릎 토큰이 아닌 다른 토큰이 오류 문구에 섞여도 가린다.
_HF_TOKEN_SHAPE = re.compile(r"hf_[A-Za-z0-9]{20,}")


def redact_secrets(text: str) -> str:
    """문장에서 무릎 토큰(어떤 모양이든)과 HF 토큰처럼 생긴 문자열을 가린다. 나머지는 그대로 둔다."""
    token = hf_token()
    if token:
        text = text.replace(token, "***")
    return _HF_TOKEN_SHAPE.sub("hf_***", text)


def safe_failure_text(exc: BaseException) -> str:
    """예외를 '종류: 가린 문구' 한 줄로 만든다. 내부 오류 기록·내보낼 예외에 쓴다."""
    return f"{type(exc).__name__}: {redact_secrets(str(exc))}"


def load_on_startup() -> bool:
    return get_setting("LOAD_KNEE_ON_STARTUP", "0") == "1"


# ── 대표 슬라이스 채점(비활성 골격) 설정. 모두 선택이며 비어 있으면 안전한 쪽(허용하지 않음)으로 동작한다 ──
def asset_roots() -> tuple[str, ...]:
    """슬라이드 문제 이미지 경로로 허용하는 루트 목록(KNEE_ASSET_ROOTS, 쉼표로 구분).

    각 루트는 '/'로 시작하고 '/'로 끝나야 하며 '..'를 포함하면 안 된다. 조건에 맞지 않는 항목은 버린다.
    설정이 없으면 빈 튜플이다. 그러면 어떤 슬라이드 경로도 허용하지 않아 슬라이드 문제는 채점하지 않는다(실패 시 닫힘).
    """
    roots = []
    for part in get_setting("KNEE_ASSET_ROOTS").split(","):
        part = part.strip()
        if len(part) > 1 and part.startswith("/") and part.endswith("/") and ".." not in part:
            roots.append(part)
    return tuple(roots)


def problem_token_key() -> bytes | None:
    """문제 제시 토큰(problem_token) 서명 키. 32자 이상이어야 하며 세션 서명 키(SECRET_KEY)와 같으면 쓰지 않는다. 없으면 None."""
    key = get_setting("KNEE_PROBLEM_TOKEN_KEY")
    if len(key) < 32 or key == get_setting("SECRET_KEY"):
        return None
    return key.encode("utf-8")


def problem_token_ttl_s() -> int:
    """토큰 유효 시간(초). 기본 2시간, 60초~24시간으로 제한한다."""
    try:
        value = int(get_setting("KNEE_PROBLEM_TOKEN_TTL_S", "7200"))
    except ValueError:
        value = 7200
    return min(max(value, 60), 86400)


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
