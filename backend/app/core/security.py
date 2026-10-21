import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt
from app.core.config import get_settings

PBKDF2_ITERATIONS = 600_000


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256$" + str(PBKDF2_ITERATIONS) + "$" + _b64(salt) + "$" + _b64(digest)


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def issue_access_token(account_id: str, token_version: str) -> tuple[str, datetime]:
    settings = get_settings()
    if not settings.secret_key or settings.secret_key == "change-me-in-production":
        raise RuntimeError("Authentication is disabled until a unique SECRET_KEY is configured.")
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.access_token_expire_minutes)
    token = jwt.encode(
        {"sub": account_id, "ver": token_version, "iat": now, "exp": expires_at},
        settings.secret_key,
        algorithm=settings.algorithm,
    )
    return token, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.secret_key or settings.secret_key == "change-me-in-production":
        raise RuntimeError("Authentication is not configured.")
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
