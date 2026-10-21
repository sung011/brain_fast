"""
무릎 슬라이드 문제의 "목록(manifest)"과 대표 슬라이스 번호 계산 (비활성 골격의 순수 함수 모음).

설계: designs/MSN-20260930-001/REPRESENTATIVE_SLICE_CONTRACT.md 2·3-1·4-3.
이 파일은 DB·NAS·모델·torch 를 쓰지 않는다. 입력 문자열과 숫자만 검사한다.

용어
- 슬라이드 문제: study.st_image 가 JSON 배열 문자열인 문제. 배열의 순서가 슬라이드 순서다.
- slice_index(= slice_no): 등록된 배열에서의 위치(0부터). 화면의 "n번째 / N장"만 1부터 보여 준다.
- source_z: 원본 볼륨의 z 번호(0부터). 서비스 배열 위치와 다를 수 있어 반드시 명시적으로 매핑한다.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

LIST_HASH_VERSION = "ordered-paths-json-v1"
MAX_SLICES = 512        # 한 문제에 허용하는 최대 장 수(메모리·응답 크기 보호)
MAX_PATH_LENGTH = 1024
_INDEX_PATTERN = re.compile(r"(0|[1-9][0-9]{0,5})")   # 부호 없는 십진 정수 표현만(앞의 0·부호·소수·공백 불가)


class SliceManifestError(ValueError):
    """슬라이드 목록을 쓸 수 없을 때(빈 배열·중첩·중복·허용 루트 밖 경로·상한 초과 등). 사용자에게는 원인을 알리지 않는다."""


class SliceIndexError(ValueError):
    """요청의 slice_index 가 규칙에 맞지 않을 때(누락·형식·범위 밖). 400 으로 다룬다."""


@dataclass(frozen=True)
class SliceManifest:
    paths: tuple[str, ...]
    list_sha256: str

    @property
    def count(self) -> int:
        return len(self.paths)

    @property
    def version(self) -> str:
        """목록 내용에서 유도한 버전. 경로 순서·내용이 바뀌면 자동으로 달라진다(내용 주소 방식)."""
        return f"{LIST_HASH_VERSION}:{self.list_sha256}"


def list_hash(paths) -> str:
    """순서 있는 경로 배열의 SHA-256. 공백·줄바꿈만 다른 원본 JSON 은 같은 해시가 된다(재직렬화 후 계산)."""
    text = json.dumps(list(paths), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_path(path, roots: tuple[str, ...]) -> str:
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_LENGTH:
        raise SliceManifestError("경로 형식 오류")
    if path != path.strip() or "\\" in path or "://" in path or any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        raise SliceManifestError("경로 문자 오류")
    if any(segment in ("..", ".") for segment in path.split("/")):
        raise SliceManifestError("경로 상위 이동 오류")
    if not any(path.startswith(root) and len(path) > len(root) for root in roots):
        raise SliceManifestError("허용된 자산 루트 밖의 경로")
    return path


def build_manifest(st_image, roots: tuple[str, ...]) -> SliceManifest:
    """study.st_image(JSON 배열 문자열)를 검사해 목록을 만든다. 경로를 바꾸지 않고 순서를 그대로 보존한다."""
    if not isinstance(st_image, str):
        raise SliceManifestError("목록이 문자열이 아님")
    try:
        parsed = json.loads(st_image)
    except ValueError:
        raise SliceManifestError("JSON 이 아님") from None
    if not isinstance(parsed, list) or not parsed:
        raise SliceManifestError("비어 있거나 배열이 아님")
    if len(parsed) > MAX_SLICES:
        raise SliceManifestError("장 수 상한 초과")
    paths = tuple(_check_path(item, roots) for item in parsed)   # 중첩 배열·null·숫자는 여기서 거절된다
    if len(set(paths)) != len(paths):
        raise SliceManifestError("중복 경로")
    return SliceManifest(paths=paths, list_sha256=list_hash(paths))


def parse_slice_index(raw, slice_count: int | None = None) -> int:
    """폼으로 받은 slice_index 문자열을 엄격하게 정수로 바꾼다. 누락·형식 오류·범위 밖은 SliceIndexError.

    - 받는 형식: 0 또는 앞에 0이 없는 십진 정수(최대 6자리). bool·소수·음수·부호·공백·지수 표기는 거절.
    - 첫 장으로 바꿔 채점하는 식의 기본값 대체는 하지 않는다.
    """
    if raw is None or (isinstance(raw, str) and raw == ""):
        raise SliceIndexError("slice_index 가 필요합니다.")
    if not isinstance(raw, str) or not _INDEX_PATTERN.fullmatch(raw):
        raise SliceIndexError("slice_index 는 0 이상의 정수여야 합니다.")
    value = int(raw)
    if slice_count is not None and value >= slice_count:
        raise SliceIndexError("slice_index 가 슬라이드 범위를 벗어났습니다.")
    return value


def display_slice_number(slice_index: int) -> int:
    """화면에 보여 주는 번호(1부터)."""
    return slice_index + 1


# ── 대표 후보와 원본 z ↔ 배열 위치 매핑 (설계 2장) ───────────────────────────────
def _plain_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def candidate_source_z(roi_z, roi_depth, volume_slices) -> int:
    """데이터셋 ROI 범위의 가운데 후보 z = roiZ + roiDepth // 2 (짝수 깊이는 뒤쪽 가운데).

    값이 잘못되면 거절한다. 범위를 잘라 맞추거나 첫 장·무작위 장으로 대체하지 않는다.
    roiZ 는 대표 번호가 아니라 범위의 **시작**이다.
    """
    if not (_plain_int(roi_z) and _plain_int(roi_depth) and _plain_int(volume_slices)):
        raise SliceManifestError("ROI 범위 값은 정수여야 합니다.")
    if roi_z < 0 or roi_depth <= 0 or volume_slices <= 0 or roi_z + roi_depth > volume_slices:
        raise SliceManifestError("ROI 범위가 원본 슬라이스 수를 벗어났습니다.")
    return roi_z + roi_depth // 2


def validate_source_numbers(source_numbers, slice_count, volume_slices) -> list:
    """원본 번호 표 전체를 검사하고 리스트로 돌려준다(Codex 15차 015-01).

    - 길이 = 등록된 목록의 장 수(표의 i 번째 = 배열 i 번째 장의 원본 z).
    - 모든 값이 정수(bool 불가)이고 서로 다르며 0 이상 원본 볼륨 장 수 미만.
    "표 길이 = 원본 볼륨의 장 수"를 뜻하지 않는다. 일부만 등록하거나 표본을 뽑은 표도 길이가 목록과 같으면 올바르다.
    표에 후보 z 가 들어 있다는 것만으로 통과시키지 않는다.
    """
    try:
        numbers = list(source_numbers)
    except TypeError:
        raise SliceManifestError("원본 번호 표가 목록이 아닙니다.") from None
    if not (_plain_int(slice_count) and _plain_int(volume_slices)) or slice_count <= 0 or volume_slices <= 0:
        raise SliceManifestError("장 수가 올바르지 않습니다.")
    if len(numbers) != slice_count:
        raise SliceManifestError("원본 번호 표의 길이가 등록된 목록의 장 수와 다릅니다.")
    if any(not _plain_int(n) or n < 0 or n >= volume_slices for n in numbers):
        raise SliceManifestError("원본 번호가 정수가 아니거나 볼륨 범위를 벗어났습니다.")
    if len(set(numbers)) != len(numbers):
        raise SliceManifestError("원본 번호가 중복되었습니다.")
    return numbers


def source_z_to_index(source_z, source_numbers) -> int:
    """원본 z 번호를 등록 배열 위치로 바꾼다. source_numbers[i] 는 배열 i 번째 장의 원본 z.

    부분 추출·정렬·샘플링으로 두 번호가 달라져도 이 표로 명시적으로 옮긴다. 없거나 중복이면 거절한다.
    """
    numbers = list(source_numbers)
    if not _plain_int(source_z) or any(not _plain_int(n) for n in numbers) or len(set(numbers)) != len(numbers):
        raise SliceManifestError("원본 번호 표가 올바르지 않습니다.")
    if source_z not in numbers:
        raise SliceManifestError("대표 후보가 등록된 장에 없습니다.")
    return numbers.index(source_z)
