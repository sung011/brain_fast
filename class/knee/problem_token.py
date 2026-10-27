"""
문제 제시 토큰(problem_token): 서버가 학습자에게 "어느 버전의 대표 슬라이스를 보여 줬는지"를 서명해 둔 쪽지.

설계: designs/MSN-20260930-001/REPRESENTATIVE_SLICE_CONTRACT.md 4-2.

- 서명만 하고 암호화하지 않는다. 그래서 payload 에 정답 박스·진단·이메일·실명·내부 NAS 주소를 넣지 않는다.
- 서명 키는 세션 서명 키와 분리한다(config.problem_token_key). 토큰 전체를 로그에 남기지 않는다.
- 만료 전 같은 토큰의 재사용은 허용한다(사용자 결정 2026-10-06). 1회용 저장소를 두지 않는다.
- 이 토큰은 영상 바이트의 변경을 막지 않는다(그건 영상 불변성 A, 팀장·NAS 작업). 제시한 버전과 제출 대상의 결속만 증명한다.
- 학습자가 영상을 실제로 봤다는 증명이 아니다.

이 파일은 DB·NAS·모델·torch 를 쓰지 않는다. 시간·키·jti 는 호출하는 쪽이 넘겨 주므로 시험할 수 있다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets

TOKEN_VERSION = 1
_PREFIX = "kt1"
_REQUIRED = {"v", "u", "s", "mv", "sn", "ls", "is", "ri", "rv", "iat", "exp", "jti"}


class TokenInvalid(ValueError):
    """형식·서명·사용자·문제가 맞지 않을 때."""


class TokenExpired(ValueError):
    """서명은 맞지만 유효 시간이 지났을 때."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(key: bytes, body: str) -> str:
    return _b64(hmac.new(key, f"{_PREFIX}.{body}".encode("ascii"), hashlib.sha256).digest())


def issue_token(
    *,
    key: bytes,
    now: int,
    ttl_s: int,
    user_idx: int,
    study_idx: int,
    manifest_version: str,
    slice_no: int,
    slice_set_sha256: str,
    image_sha256: str,
    reference_id: int,
    reference_version: str,
    jti: str | None = None,
) -> str:
    """토큰을 만든다. 경로·박스·진단은 넣지 않는다(경로는 서버 목록에서 다시 찾는다)."""
    payload = {
        "v": TOKEN_VERSION, "u": user_idx, "s": study_idx, "mv": manifest_version, "sn": slice_no,
        "ls": slice_set_sha256, "is": image_sha256, "ri": reference_id, "rv": reference_version,
        "iat": now, "exp": now + ttl_s, "jti": jti or secrets.token_hex(8),
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{_PREFIX}.{body}.{_sign(key, body)}"


def verify_token(token, *, key: bytes, now: int, user_idx: int, study_idx: int) -> dict:
    """서명·형식·만료·사용자·문제를 확인하고 payload 를 돌려준다. 실패하면 TokenInvalid / TokenExpired.

    목록 버전·대표 번호·해시·기준 ID 를 현재 서버 값과 비교하는 일은 호출하는 쪽이 한다(바뀌었으면 '제시 변경').
    예외 문구에 토큰 내용을 담지 않는다.
    """
    if not isinstance(token, str) or len(token) > 2048:
        raise TokenInvalid("형식")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _PREFIX:
        raise TokenInvalid("형식")
    _, body, signature = parts
    if not (body.isascii() and signature.isascii()):
        raise TokenInvalid("형식")
    if not hmac.compare_digest(signature.encode("ascii", "ignore"), _sign(key, body).encode("ascii")):
        raise TokenInvalid("서명")
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, UnicodeDecodeError):
        raise TokenInvalid("본문") from None
    if not isinstance(payload, dict) or set(payload) != _REQUIRED or payload["v"] != TOKEN_VERSION:
        raise TokenInvalid("본문")
    for field in ("u", "s", "sn", "ri", "iat", "exp"):
        if not isinstance(payload[field], int) or isinstance(payload[field], bool):
            raise TokenInvalid("본문")
    if payload["u"] != user_idx or payload["s"] != study_idx:
        raise TokenInvalid("대상")
    if now >= payload["exp"]:
        raise TokenExpired("만료")
    if now < payload["iat"] - 60:   # 시계가 크게 어긋난 토큰은 받지 않는다
        raise TokenInvalid("발급 시각")
    return payload
