"""
NAS 에 올라 있는 무릎 학습 영상을 서버가 대신 가져와 **승인된 해시와 대조한 뒤** 로그인한 학습자에게만 보여 주는 도우미(비활성 골격).

왜 서버가 가져오나: 영상 바이트의 불변성을 NAS 권한에만 맡기지 않고, 읽을 때마다 승인 기준의 `image_sha256`과 대조해서 보장한다
(설계 7장 "영상 불변성"의 발표용 대체 근거). 내용이 다르면 학습자에게 보여 주지 않는다. 학습자 브라우저가 NAS 주소를 직접 보지도 않는다.

안전 규칙
- 가져올 주소는 설정의 기준 주소(`KNEE_NAS_IMAGE_BASE_URL`, https 만) + 문제 목록에 등록된 서버 경로뿐이다. 경로는 `/` 로 시작하고 `..`·`//`·`\\`·
  제어 문자·`?`·`#`·스킴이 없어야 하며, 최종 주소의 호스트가 기준 주소의 호스트와 같아야 한다(다른 서버로 새지 않게).
- 리다이렉트는 따라가지 않는다. 시간 제한과 크기 제한(8 MiB)이 있다. PNG 만 받는다(서명·헤더 크기 확인, 한 변 4096 이하).
- 내려받은 바이트의 SHA-256 이 승인된 값과 다르면 `AssetMismatch`. 가져오지 못하면 `AssetUnavailable`(원인·주소·NAS 응답 내용은 담지 않는다).
- 검증을 통과한 영상만 **메모리에** 잠깐 보관한다(디스크 저장 없음, 최대 8장, 10분). 키가 (경로, 해시)라서 승인이 바뀌면 다시 검증하고, 10분이 지나면 NAS 에서 다시 가져와 다시 검증한다.
- 설정이 비어 있으면 아무것도 하지 않는다(닫힘). 시험에서는 가짜 NAS 서버(로컬 http)와 `allow_http` 를 쓴다.

이 파일은 DB·모델·torch 를 쓰지 않는다.
"""
from __future__ import annotations

import hashlib
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass

from . import config

MAX_BYTES = 8 * 1024 * 1024
TIMEOUT_S = 10.0
CACHE_ITEMS = 8
CACHE_TTL_S = 600.0   # 검증한 바이트를 메모리에 두는 시간. 지나면 NAS 에서 다시 가져와 다시 검증한다
MAX_SIDE = 4096
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class AssetUnavailable(Exception):
    """영상을 가져오지 못했다(설정 없음·통신·상태 코드·크기·형식). 원인 문자열에 주소·응답 내용을 담지 않는다."""


class AssetMismatch(Exception):
    """내려받은 영상의 내용이 승인된 해시와 다르다."""


@dataclass(frozen=True)
class VerifiedImage:
    data: bytes
    width: int
    height: int
    sha256: str


def png_size(data: bytes) -> tuple[int, int]:
    """PNG 의 가로·세로. PNG 가 아니거나 크기가 비정상이면 AssetUnavailable."""
    if len(data) < 33 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise AssetUnavailable("not a png")
    width, height = struct.unpack(">II", data[16:24])
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
        raise AssetUnavailable("bad png size")
    return width, height


def check_server_path(image_path) -> str:
    r"""문제 목록에 등록된 서버 경로가 안전한 모양인지 검사한다(NAS 주소·로컬 폴더 공용). 어긋나면 AssetUnavailable.

    `/` 로 시작, 앞 두 글자가 `//` 아님, 역슬래시·`?`·`#`·`%`·`:`(드라이브·대체 스트림)·제어 문자(DEL 포함)·스킴 없음,
    `..`·`.` 구간 없음, 1024자 이하.
    """
    path = image_path
    if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or len(path) > 1024
            or any(ch in path for ch in ("\\", "?", "#", "%", ":", "\x7f")) or any(ord(ch) < 32 for ch in path)
            or any(seg in ("..", ".") for seg in path.split("/"))):
        raise AssetUnavailable("bad path")
    return path


def matches_approved_size(reference, image) -> bool:
    """검증된 PNG 의 치수가 승인 기준에 기록된 치수와 같은가. 승인에 치수가 없으면 False(영상 공급원을 쓸 때는 치수가 필수)."""
    width, height = getattr(reference, "image_width", None), getattr(reference, "image_height", None)
    return width is not None and height is not None and (image.width, image.height) == (width, height)


def build_url(base_url: str, image_path: str, *, allow_http: bool = False) -> str:
    """기준 주소 + 서버 경로로 가져올 주소를 만든다. 규칙에 어긋나면 AssetUnavailable."""
    parts = urllib.parse.urlsplit(base_url or "")
    if parts.scheme not in (("https", "http") if allow_http else ("https",)) or not parts.hostname or parts.username or parts.password:
        raise AssetUnavailable("bad base")
    if parts.query or parts.fragment:
        raise AssetUnavailable("bad base")
    path = check_server_path(image_path)
    url = base_url.rstrip("/") + urllib.parse.quote(path, safe="/")
    if urllib.parse.urlsplit(url).netloc != parts.netloc:
        raise AssetUnavailable("host changed")
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):   # 어떤 리다이렉트도 따라가지 않는다
        return None


def default_fetch(url: str, *, timeout: float = TIMEOUT_S, max_bytes: int = MAX_BYTES) -> bytes:
    """URL 에서 바이트를 받는다(리다이렉트 거부·시간 제한·크기 제한·image/png 만). 실패는 모두 AssetUnavailable."""
    opener = urllib.request.build_opener(_NoRedirect())
    request = urllib.request.Request(url, headers={"Accept": "image/png", "User-Agent": "medinote-knee-asset/1"})
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise AssetUnavailable("status")
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "image/png":
                raise AssetUnavailable("content type")
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise AssetUnavailable("too large")
            data = response.read(max_bytes + 1)
    except AssetUnavailable:
        raise
    except Exception:   # 통신 오류·시간 초과·HTTP 오류(리다이렉트 포함)의 원문은 남기지 않는다
        raise AssetUnavailable("fetch failed") from None
    if len(data) > max_bytes:
        raise AssetUnavailable("too large")
    return data


class NasImageSource:
    """기준 주소 하나에서 영상을 가져와 해시를 대조하고 메모리에 잠깐 보관한다."""

    def __init__(self, base_url: str, *, fetch=None, allow_http: bool = False, cache_items: int = CACHE_ITEMS,
                 cache_ttl_s: float = CACHE_TTL_S, clock=None):
        self.base_url = base_url
        self.allow_http = allow_http
        self._fetch = fetch or default_fetch
        self._cache: OrderedDict[tuple[str, str], tuple[VerifiedImage, float]] = OrderedDict()
        self._cache_items = cache_items
        self._ttl = cache_ttl_s
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()

    def get(self, image_path: str, expected_sha256: str) -> VerifiedImage:
        if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
            raise AssetUnavailable("bad hash")
        key = (image_path, expected_sha256)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                if self._clock() - cached[1] <= self._ttl:
                    self._cache.move_to_end(key)
                    return cached[0]
                del self._cache[key]   # 오래된 검증 결과는 버리고 NAS 에서 다시 가져와 다시 검증한다
        url = build_url(self.base_url, image_path, allow_http=self.allow_http)
        data = self._fetch(url)
        if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_BYTES:
            raise AssetUnavailable("bad body")
        data = bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha256:
            raise AssetMismatch("hash differs")
        width, height = png_size(data)
        image = VerifiedImage(data=data, width=width, height=height, sha256=digest)
        with self._lock:
            self._cache[key] = (image, self._clock())
            while len(self._cache) > self._cache_items:
                self._cache.popitem(last=False)
        return image

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()


# ── 설정에서 만드는 기본 공급원 ─────────────────────────────────────────────────────
_singleton: tuple[tuple[str, bool], NasImageSource] | None = None
_singleton_lock = threading.Lock()


def source_from_settings() -> NasImageSource | None:
    """`KNEE_NAS_IMAGE_BASE_URL`(https 기준 주소)이 설정돼 있으면 공급원, 없거나 규칙에 어긋나면 None(닫힘).

    `KNEE_NAS_ALLOW_HTTP=1` 은 시험(로컬 가짜 NAS) 전용이며 운영에서는 설정하지 않는다.
    같은 설정이면 같은 공급원(캐시 공유)을 돌려준다.
    """
    global _singleton
    base = config.source_setting("KNEE_NAS_IMAGE_BASE_URL")   # 환경변수에 빈 값으로 두면 .env 값과 무관하게 비활성(FIVE-06)
    allow_http = config.get_setting("KNEE_NAS_ALLOW_HTTP", "0") == "1"
    if not base:
        return None
    try:
        build_url(base, "/probe", allow_http=allow_http)   # 기준 주소 형식 확인
    except AssetUnavailable:
        return None
    with _singleton_lock:
        if _singleton is None or _singleton[0] != (base, allow_http):
            _singleton = ((base, allow_http), NasImageSource(base, allow_http=allow_http))
        return _singleton[1]
