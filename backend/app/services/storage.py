"""Private Supabase Storage adapter for caregiver evidence files.

Credentials are read only from backend environment settings. File bytes are never
written to the ephemeral application filesystem.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import HTTPException

from app.core.config import get_settings


def _storage_config():
    settings = get_settings()
    base_url = settings.supabase_url.strip().rstrip("/")
    service_key = settings.supabase_service_role_key.strip()
    bucket = settings.supabase_storage_bucket.strip()
    if not base_url or not service_key or not bucket:
        raise HTTPException(
            status_code=503,
            detail=(
                "Private photo storage is not configured. The service administrator must "
                "configure SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, and "
                "SUPABASE_STORAGE_BUCKET before photo uploads are available."
            ),
        )
    return base_url, service_key, bucket


def _object_url(base_url: str, bucket: str, object_path: str) -> str:
    return (
        f"{base_url}/storage/v1/object/"
        f"{quote(bucket, safe='')}/{quote(object_path, safe='/')}"
    )


def upload_private_object(object_path: str, content_type: str, data: bytes) -> None:
    base_url, service_key, bucket = _storage_config()
    request = Request(
        _object_url(base_url, bucket, object_path),
        data=data,
        headers={
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Content-Type": content_type,
            "x-upsert": "false",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            if not 200 <= response.status < 300:
                raise HTTPException(status_code=502, detail="Private photo storage rejected the upload")
    except HTTPError as exc:
        # Never return provider response bodies: they may contain infrastructure details.
        raise HTTPException(status_code=502, detail="Private photo storage rejected the upload") from exc
    except URLError as exc:
        raise HTTPException(status_code=503, detail="Private photo storage is unavailable") from exc


def delete_private_object(object_path: str) -> None:
    """Best-effort cleanup if the database write fails after storage upload."""
    try:
        base_url, service_key, bucket = _storage_config()
        request = Request(
            _object_url(base_url, bucket, object_path),
            headers={"Authorization": f"Bearer {service_key}", "apikey": service_key},
            method="DELETE",
        )
        with urlopen(request, timeout=10):
            pass
    except Exception:
        # Cleanup must not hide the original database error. Orphan cleanup can be
        # reconciled later by comparing storage objects to evidence.original_file_path.
        return


def create_signed_object_url(object_path: str, expires_in: int = 300) -> str:
    base_url, service_key, bucket = _storage_config()
    endpoint = (
        f"{base_url}/storage/v1/object/sign/"
        f"{quote(bucket, safe='')}/{quote(object_path, safe='/')}"
    )
    request = Request(
        endpoint,
        data=json.dumps({"expiresIn": max(60, min(expires_in, 900))}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Private photo storage could not create a viewing link") from exc

    signed_path = payload.get("signedURL") if isinstance(payload, dict) else None
    if not isinstance(signed_path, str) or not signed_path:
        raise HTTPException(status_code=502, detail="Private photo storage returned an invalid viewing link")
    if signed_path.startswith("http://") or signed_path.startswith("https://"):
        return signed_path
    if signed_path.startswith("/storage/v1/"):
        return f"{base_url}{signed_path}"
    return f"{base_url}/storage/v1/{signed_path.lstrip('/')}"
