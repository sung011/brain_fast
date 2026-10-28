"""
네이버 클라우드 Object Storage 업로드.

S3 호환 API(한국 리전)로 파일을 올리고, 브라우저 <img>가 열 수 있게
객체 ACL을 public-read로 둔다. DB에는 버킷 주소를 뺀 웹 경로만 남긴다.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from config import settings


class StorageNotConfiguredError(RuntimeError):
    """NCP_ACCESS_KEY / NCP_SECRET_KEY / NCP_BUCKET 이 비어 있을 때."""


class StorageUploadError(RuntimeError):
    """Object Storage 업로드 실패."""


def _content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def public_object_path(remote_dir: str | None, filename: str) -> tuple[str, str]:
    """
    저장 폴더와 파일 이름 → (객체 키, DB용 웹 경로).

    /web/mu_shop/public/stylesheets/assets/a.png
      → 키 stylesheets/assets/a.png
      → 웹 경로 /stylesheets/assets/a.png
    """
    public_root = (settings.nas_public_root or "").rstrip("/")
    folder = (remote_dir or settings.nas_base_path or "/stylesheets/assets").strip()
    folder = folder.replace("\\", "/")
    if not folder.startswith("/"):
        folder = f"/{folder}"
    if public_root and (folder == public_root or folder.startswith(public_root + "/")):
        folder = folder[len(public_root) :] or "/"
    safe_name = filename.replace("\\", "/").split("/")[-1] or "upload.bin"
    web_path = f"{folder.rstrip('/')}/{safe_name}"
    return web_path.lstrip("/"), web_path


class ObjectStorageService:
    """네이버 클라우드 Object Storage (S3 호환) 업로드."""

    def __init__(self) -> None:
        self.access_key = settings.ncp_access_key or ""
        self.secret_key = settings.ncp_secret_key or ""
        self.bucket = (settings.ncp_bucket or "").strip()
        self.endpoint = (
            settings.ncp_endpoint or "https://kr.object.ncloudstorage.com"
        ).rstrip("/")
        self.region = settings.ncp_region or "kr-standard"
        self.base_path = (
            settings.nas_base_path or "/stylesheets/assets"
        ).rstrip("/") or "/stylesheets/assets"
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.access_key and self.secret_key and self.bucket)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise StorageNotConfiguredError(
                "Object Storage가 설정되지 않았습니다. "
                ".env 에 NCP_ACCESS_KEY, NCP_SECRET_KEY, NCP_BUCKET 을 넣으세요."
            )

    def _s3(self):
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    # 기본 체크섬 헤더는 네이버 클라우드가 PutObject를 거부한다.
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
            )
        return self._client

    def to_public_path(self, remote_path: str) -> str:
        """객체 키 또는 전체 경로 → DB에 넣는 웹 경로."""
        path = (remote_path or "").replace("\\", "/")
        root = (settings.nas_public_root or "").rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        elif root and path.startswith(root):
            path = path[len(root) :]
        bucket_prefix = f"{self.bucket}/"
        if self.bucket and path.startswith(bucket_prefix):
            path = path[len(bucket_prefix) :]
        if not path.startswith("/"):
            path = f"/{path}" if path else "/"
        return path

    def _put_object(self, key: str, content: bytes, content_type: str) -> None:
        try:
            self._s3().put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ACL="public-read",
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUploadError(f"Object Storage 업로드 실패: {exc}") from exc

    async def upload_bytes(
        self,
        content: bytes,
        filename: str,
        *,
        remote_dir: str | None = None,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """파일을 버킷에 올린다. remote_dir 예: /stylesheets/assets"""
        del overwrite  # 같은 키는 덮어쓴다.
        self._require_enabled()
        key, web_path = public_object_path(remote_dir, filename)
        await asyncio.to_thread(
            self._put_object, key, content, _content_type(filename)
        )
        return {
            "ok": True,
            "remote_path": web_path,
            "object_key": key,
            "filename": web_path.rsplit("/", 1)[-1],
            "bytes": len(content),
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
        {base_path}/{날짜}/{request_id}/ 아래에 원본·overlay·result.json 저장.
        """
        self._require_enabled()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        folder = f"{self.base_path}/{stamp}/{request_id}"
        uploaded: list[dict[str, Any]] = []

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
        _, folder_web = public_object_path(folder, "_")
        return {
            "ok": all(item.get("ok") for item in uploaded),
            "folder": folder_web.rsplit("/", 1)[0],
            "files": uploaded,
        }

    async def health(self) -> dict[str, Any]:
        """버킷 조회가 되는지 확인."""
        if not self.enabled:
            return {
                "ok": False,
                "configured": False,
                "url": self.endpoint,
                "base_path": self.base_path,
                "error": "NCP_ACCESS_KEY / NCP_SECRET_KEY / NCP_BUCKET 미설정",
            }
        try:
            await asyncio.to_thread(
                lambda: self._s3().head_bucket(Bucket=self.bucket)
            )
            return {
                "ok": True,
                "configured": True,
                "url": self.endpoint,
                "base_path": self.base_path,
            }
        except (BotoCoreError, ClientError) as exc:
            return {
                "ok": False,
                "configured": True,
                "url": self.endpoint,
                "base_path": self.base_path,
                "error": str(exc),
            }


storage_service = ObjectStorageService()
