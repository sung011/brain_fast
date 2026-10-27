"""
서버 폴더에 둔 무릎 학습 영상을 **한 번 읽은 바이트로 검증한 뒤 그대로** 로그인한 학습자에게 보여 주는 도우미(비활성 골격, 발표 단일 서버용).

NAS 공급원(nas_assets.py)과 같은 약속을 지킨다: 승인 기준의 `image_sha256` 과 같은 바이트만, 승인된 PNG 치수일 때만 내보낸다.
이 방식은 "사용할 때 내용을 결속"하는 검사일 뿐이며, 파일을 영구 보존하거나 추가 전용으로 지켜 주는 NAS 불변성(설계 7장 A)과 같은 보장이 아니다.

규칙
- 폴더(`KNEE_IMAGE_DIR`)는 절대 경로의 실제 폴더여야 하고, 앱의 정적 파일 폴더(`assets/`)나 템플릿 폴더 **안**이거나 그것을 **포함하는 상위 폴더**여서는
  안 된다(정적 공개 금지). 실제로 여는 파일이 그 폴더 안이면 root 설정과 별개로 한 번 더 거절한다.
- 파일은 문제 목록에 등록된 서버 경로(`check_server_path` 통과)로만 연다. 요청이 낸 경로를 파일 접근 권한으로 쓰지 않는다.
  `..`·절대/UNC/드라이브/대체 스트림·역슬래시를 거절하고, 폴더 밖으로 나가는 경로(실제 경로 기준)와 **심볼릭 링크·정션(재해석 지점)** 을 거절한다.
- 파일은 한 번만 열고 그 핸들에서 일반 파일인지·크기(8 MiB 이하)를 확인한 뒤 읽는다. 읽은 바이트의 SHA-256 과 PNG 치수를 검증하고,
  **그 바이트를 그대로** 돌려준다(검사 후 다시 열어 보내지 않는다). 읽는 도중 파일이 교체돼도 해시가 맞는 바이트만 나간다.
- 내용이 다르면 `AssetMismatch`, 열 수 없거나 형식·크기가 틀리면 `AssetUnavailable`(경로·폴더 위치는 담지 않는다).
- 디스크에 복사하거나 캐시하지 않는다(매번 읽는다). 설정이 비어 있으면 아무것도 하지 않는다(닫힘).

이 파일은 DB·모델·torch 를 쓰지 않으며, 로그인·DB CRUD 를 하지 않는다.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from . import config
from .nas_assets import MAX_BYTES, AssetMismatch, AssetUnavailable, VerifiedImage, check_server_path, png_size  # noqa: F401  (예외·형식을 공용으로 쓴다)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_REPARSE_POINT = 0x400   # Windows FILE_ATTRIBUTE_REPARSE_POINT (심볼릭 링크·정션 등)
_APP_ROOT = Path(__file__).resolve().parents[2]
_FORBIDDEN_ROOTS = (_APP_ROOT / "assets", _APP_ROOT / "templates")


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


class LocalImageSource:
    """폴더 하나에서 영상을 읽어 해시를 대조한다."""

    def __init__(self, root):
        root_path = Path(root)
        if not root_path.is_absolute():
            raise AssetUnavailable("root must be absolute")
        try:
            resolved = root_path.resolve(strict=True)
        except OSError:
            raise AssetUnavailable("root missing") from None
        if not resolved.is_dir():
            raise AssetUnavailable("root not a directory")
        # 정적 공개 폴더 안이어도, 정적 공개 폴더를 **포함하는** 상위 폴더여도 거절한다(상위 폴더를 root 로 잡으면 공개 폴더 안의 파일이
        # 이 인증 경로로도 읽히고 동시에 인증 없는 정적 주소(/assets)로도 열려 있게 된다).
        for forbidden in _FORBIDDEN_ROOTS:
            public = forbidden.resolve()
            if _inside(resolved, public) or _inside(public, resolved):
                raise AssetUnavailable("root overlaps a public folder")
        self.root = resolved

    def _open_path(self, image_path: str) -> Path:
        """등록된 서버 경로를 폴더 아래의 실제 파일 경로로 바꾼다. 재해석 지점·폴더 밖은 거절한다."""
        parts = [seg for seg in check_server_path(image_path).split("/") if seg]
        if not parts:
            raise AssetUnavailable("empty path")
        current = self.root
        for seg in parts:
            current = current / seg
            try:
                info = os.lstat(current)
            except OSError:
                raise AssetUnavailable("missing") from None
            if _is_reparse(info):
                raise AssetUnavailable("link")
        try:
            real = current.resolve(strict=True)
        except OSError:
            raise AssetUnavailable("missing") from None
        if not _inside(real, self.root):
            raise AssetUnavailable("outside root")
        if any(_inside(real, forbidden.resolve()) for forbidden in _FORBIDDEN_ROOTS):
            raise AssetUnavailable("target inside a public folder")   # 실제 접근 대상이 정적 공개 폴더 안이면 거절(root 설정과 별개로 한 번 더)
        return current

    def get(self, image_path: str, expected_sha256: str) -> VerifiedImage:
        if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
            raise AssetUnavailable("bad hash")
        path = self._open_path(image_path)
        try:
            with open(path, "rb") as handle:   # 한 번만 연다
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                    raise AssetUnavailable("not a regular file or too large")
                data = handle.read(MAX_BYTES + 1)
        except AssetUnavailable:
            raise
        except OSError:
            raise AssetUnavailable("read failed") from None
        if len(data) > MAX_BYTES:
            raise AssetUnavailable("too large")
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha256:
            raise AssetMismatch("hash differs")
        width, height = png_size(data)
        return VerifiedImage(data=data, width=width, height=height, sha256=digest)

    def clear_cache(self) -> None:   # NAS 공급원과 같은 모양(캐시가 없으므로 할 일 없음)
        return None


def source_from_settings() -> LocalImageSource | None:
    """`KNEE_IMAGE_DIR`(절대 경로의 실제 폴더)이 설정돼 있고 규칙에 맞으면 공급원, 아니면 None(닫힘)."""
    folder = config.source_setting("KNEE_IMAGE_DIR")   # 환경변수에 빈 값으로 두면 .env 값과 무관하게 비활성(FIVE-06)
    if not folder:
        return None
    try:
        return LocalImageSource(folder)
    except AssetUnavailable:
        return None
