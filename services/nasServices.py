"""
Synology NAS (File Station API) 업로드 서비스.

DSM HTTPS(예: https://host:5001) 에 로그인 후
공유 폴더 경로로 파일을 올린다.

속도: SID·HTTP 클라이언트를 재사용해 매 요청 로그인/TLS 비용을 줄인다.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from config import settings


class NasNotConfiguredError(RuntimeError):
    """NAS_URL / NAS_USER / NAS_PASSWORD 가 비어 있을 때."""


class NasUploadError(RuntimeError):
    """Synology API 호출 실패."""


def _bool_str(value: bool) -> str:
    return "true" if value else "false"


class SynologyNasService:
    """Synology File Station 로그인·업로드 (세션 재사용)."""

    # SID 재사용 시간 (초). DSM 세션보다 짧게 잡는다.
    _SID_TTL_SEC = 25 * 60

    def __init__(self) -> None:
        self.base_url = (settings.nas_url or "").rstrip("/")
        self.user = settings.nas_user or ""
        self.password = settings.nas_password or ""
        self.base_path = (
            settings.nas_base_path or "/stylesheets/assets"
        ).rstrip("/") or "/"
        self.verify_ssl = settings.nas_verify_ssl
        self.timeout = settings.nas_timeout
        self.public_root = (
            getattr(settings, "nas_public_root", None) or "/web/mu_shop/public"
        ).rstrip("/")

        self._client: httpx.AsyncClient | None = None
        self._sid: str | None = None
        self._sid_expires_at: float = 0.0
        self._auth_version: int = 3
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.user and self.password)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise NasNotConfiguredError(
                "NAS가 설정되지 않았습니다. .env 에 NAS_URL, NAS_USER, NAS_PASSWORD 를 넣으세요."
            )

    async def _get_client(self) -> httpx.AsyncClient:
        """Keep-alive 연결을 재사용하는 공유 클라이언트."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                verify=self.verify_ssl,
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                follow_redirects=True,
                limits=httpx.Limits(
                    max_keepalive_connections=5,
                    max_connections=10,
                    keepalive_expiry=60.0,
                ),
            )
        return self._client

    @staticmethod
    def _raise_if_api_error(payload: dict[str, Any], action: str) -> None:
        if payload.get("success"):
            return
        err = payload.get("error") or {}
        code = err.get("code")
        raise NasUploadError(f"Synology {action} 실패 (code={code}): {payload}")

    def _sid_valid(self) -> bool:
        return bool(self._sid) and time.monotonic() < self._sid_expires_at

    async def _login_raw(self, client: httpx.AsyncClient) -> str:
        """새 SID 발급. 이전에 성공한 auth version을 먼저 시도."""
        versions = [self._auth_version] + [
            v for v in (3, 6, 7) if v != self._auth_version
        ]
        data: dict[str, Any] = {}
        for version in versions:
            resp = await client.get(
                "/webapi/auth.cgi",
                params={
                    "api": "SYNO.API.Auth",
                    "version": version,
                    "method": "login",
                    "account": self.user,
                    "passwd": self.password,
                    "session": "FileStation",
                    "format": "sid",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                sid = (data.get("data") or {}).get("sid")
                if sid:
                    self._auth_version = version
                    return str(sid)
            code = (data.get("error") or {}).get("code")
            if code in (400, 401, 402, 403, 404):
                self._raise_if_api_error(data, "login")
        self._raise_if_api_error(data, "login")
        raise NasUploadError("Synology login: SID를 받지 못했습니다.")

    async def _ensure_sid(self, client: httpx.AsyncClient, *, force: bool = False) -> str:
        async with self._lock:
            if not force and self._sid_valid():
                return str(self._sid)
            sid = await self._login_raw(client)
            self._sid = sid
            self._sid_expires_at = time.monotonic() + self._SID_TTL_SEC
            return sid

    def _invalidate_sid(self) -> None:
        self._sid = None
        self._sid_expires_at = 0.0

    async def health(self) -> dict[str, Any]:
        """로그인 가능 여부만 확인."""
        if not self.enabled:
            return {
                "ok": False,
                "configured": False,
                "error": "NAS_URL / NAS_USER / NAS_PASSWORD 미설정",
            }
        try:
            client = await self._get_client()
            await self._ensure_sid(client, force=True)
            return {
                "ok": True,
                "configured": True,
                "url": self.base_url,
                "base_path": self.base_path,
            }
        except Exception as exc:
            self._invalidate_sid()
            return {
                "ok": False,
                "configured": True,
                "url": self.base_url,
                "base_path": self.base_path,
                "error": str(exc),
            }

    def _resolve_upload_dir(self, remote_dir: str | None) -> str:
        """웹 상대 경로면 /web/mu_shop/public 을 앞에 붙인다."""
        path = (remote_dir or self.base_path or "/stylesheets/assets").strip().replace(
            "\\", "/"
        )
        if not path.startswith("/"):
            path = f"/{path}"
        root = self.public_root
        if path.startswith(root + "/") or path == root:
            return path.rstrip("/") or root
        return f"{root}{path}".rstrip("/")

    async def _upload_once(
        self,
        client: httpx.AsyncClient,
        sid: str,
        *,
        content: bytes,
        safe_name: str,
        dest: str,
        overwrite: bool,
    ) -> dict[str, Any]:
        files = {
            "path": (None, dest),
            "create_parents": (None, "true"),
            "overwrite": (None, _bool_str(overwrite)),
            "filename": (safe_name, content, "application/octet-stream"),
        }
        resp = await client.post(
            "/webapi/entry.cgi",
            params={
                "api": "SYNO.FileStation.Upload",
                "version": "2",
                "method": "upload",
                "_sid": sid,
            },
            files=files,
        )
        resp.raise_for_status()
        return resp.json()

    async def upload_bytes(
        self,
        content: bytes,
        filename: str,
        *,
        remote_dir: str | None = None,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """단일 파일을 NAS 폴더에 업로드. remote_dir 예: /stylesheets/assets"""
        self._require_enabled()
        dest = self._resolve_upload_dir(remote_dir)
        safe_name = filename.replace("\\", "/").split("/")[-1] or "upload.bin"
        client = await self._get_client()

        sid = await self._ensure_sid(client)
        data = await self._upload_once(
            client,
            sid,
            content=content,
            safe_name=safe_name,
            dest=dest,
            overwrite=overwrite,
        )
        # 세션 만료(119 등)면 한 번만 재로그인 후 재시도
        if not data.get("success"):
            code = (data.get("error") or {}).get("code")
            if code in (105, 106, 119, 120):
                self._invalidate_sid()
                sid = await self._ensure_sid(client, force=True)
                data = await self._upload_once(
                    client,
                    sid,
                    content=content,
                    safe_name=safe_name,
                    dest=dest,
                    overwrite=overwrite,
                )
        self._raise_if_api_error(data, "upload")
        remote_path = f"{dest}/{safe_name}"
        return {
            "ok": True,
            "remote_path": remote_path,
            "filename": safe_name,
            "bytes": len(content),
            "data": data.get("data"),
        }

    async def upload_analysis_bundle(
        self,
        *,
        request_id: str,
        image_bytes: bytes,
        image_filename: str,
        result: dict[str, Any],
        overlay_png_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        """
        분석 결과 묶음 업로드.
        {NAS_BASE_PATH}/{request_id}/ 아래에 원본·overlay·result.json 저장.
        """
        self._require_enabled()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        folder = f"{self.base_path}/{stamp}/{request_id}"
        uploaded: list[dict[str, Any]] = []

        # SID 한 번만 확보한 뒤 연속 업로드
        client = await self._get_client()
        await self._ensure_sid(client)

        safe_img = image_filename.replace("\\", "/").split("/")[-1] or "roi.png"
        uploaded.append(
            await self.upload_bytes(image_bytes, safe_img, remote_dir=folder)
        )
        if overlay_png_bytes:
            uploaded.append(
                await self.upload_bytes(
                    overlay_png_bytes, "overlay.png", remote_dir=folder
                )
            )
        result_json = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
        uploaded.append(
            await self.upload_bytes(result_json, "result.json", remote_dir=folder)
        )
        return {
            "ok": all(item.get("ok") for item in uploaded),
            "folder": self._resolve_upload_dir(folder),
            "files": uploaded,
        }


nas_service = SynologyNasService()
