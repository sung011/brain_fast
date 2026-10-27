"""
화면 캡처 한 장(PNG/JPEG)을 안전하게 읽어 회색조 배열로 바꾼다.

- 파일 종류는 확장자·파일 이름·Content-Type 이 아니라 앞부분 바이트(매직)로 판단한다. 파일 이름은 아예 받지 않는다.
- 크기는 그림을 펼치기 전에 헤더로 먼저 확인한다(한 변 64~1024px, 총 1,048,576px 이하).
- 움직이는 이미지(APNG, 여러 프레임)는 거절한다.
- EXIF 등 메타데이터는 읽지도 저장하지도 않는다. 화소만 회색조로 바꿔 넘긴다.
- 색상 → 회색조 변환은 Pillow 의 convert("L")(ITU-R 601 가중치)를 쓰고, 투명한 이미지는 검은 배경에 합성한다.
이 파일은 torch 를 불러오지 않는다.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np

MIN_SIDE = 64
MAX_SIDE = 1024
MAX_PIXELS = 1_048_576

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_ALLOWED_MODES = {"L", "RGB", "RGBA", "LA", "P"}


class ImageRejected(Exception):
    """이미지를 받을 수 없는 이유. status/code 는 API 오류 응답에, message 는 고정 문구로 쓴다."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class DecodedImage:
    gray: np.ndarray  # (H, W) uint8
    width: int
    height: int
    format: str       # "PNG" | "JPEG"
    steps: dict       # 어떤 변환을 거쳤는지(결과 메타데이터용)


def decode_screen_image(data: bytes) -> DecodedImage:
    if not data:
        raise ImageRejected(422, "empty_image", "빈 파일입니다.")
    if not (data.startswith(_PNG_MAGIC) or data.startswith(_JPEG_MAGIC)):
        raise ImageRejected(415, "unsupported_media_type", "PNG 또는 JPEG 이미지만 올릴 수 있습니다.")

    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size  # 헤더만 읽은 상태(아직 펼치지 않음)
        fmt = image.format
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ImageRejected(422, "decode_failed", "이미지를 읽을 수 없습니다.") from None

    if fmt not in ("PNG", "JPEG"):
        raise ImageRejected(415, "unsupported_media_type", "PNG 또는 JPEG 이미지만 올릴 수 있습니다.")
    if width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
        raise ImageRejected(413, "image_too_large", "이미지가 너무 큽니다. 한 변 1024px 이하, 총 약 100만 화소 이하로 올려 주세요.")
    if min(width, height) < MIN_SIDE:
        raise ImageRejected(422, "image_too_small", "이미지가 너무 작습니다. 한 변이 64px 이상이어야 합니다.")
    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
        raise ImageRejected(422, "animated_image", "움직이는 이미지나 여러 프레임 이미지는 받을 수 없습니다.")
    if image.mode not in _ALLOWED_MODES:
        raise ImageRejected(422, "unsupported_pixel_format", "지원하지 않는 화소 형식입니다.")

    try:
        image.load()
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
            image = Image.alpha_composite(background, rgba)
        gray = np.asarray(image.convert("L"), dtype=np.uint8).copy()
    except Exception:
        raise ImageRejected(422, "decode_failed", "이미지를 읽을 수 없습니다.") from None

    if gray.ndim != 2 or gray.size == 0:
        raise ImageRejected(422, "decode_failed", "이미지를 읽을 수 없습니다.")
    if int(gray.min()) == int(gray.max()):
        raise ImageRejected(422, "blank_image", "내용이 없는(한 가지 밝기뿐인) 이미지입니다.")

    return DecodedImage(
        gray=gray, width=width, height=height, format=fmt,
        steps={"grayscale": "pillow_L_itu_r_601", "alpha": "black_background", "resize": "none", "metadata": "discarded"},
    )
