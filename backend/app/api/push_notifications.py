"""Caregiver-scoped Web Push registration.

Subscription payloads are encrypted at rest and never returned by the API.
"""
import base64
import hashlib
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.collaboration import _scope
from app.core.config import get_settings
from app.core.database import get_db
from app.models.notifications import CarePushSubscription
from app.services.web_push_config import vapid_configuration_error

router = APIRouter(tags=["notifications"])


class PushSubscriptionPayload(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2048)
    keys: dict[str, str]

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value):
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        allowed = (
            host == "fcm.googleapis.com"
            or host.endswith(".push.services.mozilla.com")
            or host.endswith(".push.apple.com")
            or host.endswith(".notify.windows.com")
        )
        if parsed.scheme != "https" or not allowed or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Push endpoint must use a supported HTTPS push-service host")
        return value

    @field_validator("keys")
    @classmethod
    def validate_keys(cls, value):
        if not value.get("p256dh") or not value.get("auth"):
            raise ValueError("Push subscription requires p256dh and auth keys")
        if len(value["p256dh"]) > 256 or len(value["auth"]) > 256:
            raise ValueError("Push subscription key is too long")
        return value


class PushSubscriptionDelete(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2048)


def _vapid_configuration_ready() -> bool:
    return vapid_configuration_error(get_settings()) is None


def _cipher() -> Fernet:
    secret = get_settings().secret_key or "local-development-only-not-for-production"
    key = base64.urlsafe_b64encode(
        hashlib.sha256((secret + ":solenos-web-push-subscriptions:v1").encode("utf-8")).digest()
    )
    return Fernet(key)


def _endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


@router.get("/notifications/push/public-key")
def get_web_push_public_key(request: Request, db: Session = Depends(get_db)):
    _scope(request, db)
    settings = get_settings()
    key = settings.vapid_public_key.strip()
    if not _vapid_configuration_ready():
        # Capability discovery is not a failed request: in-app notifications
        # remain available while Web Push credentials are not configured.
        return {"available": False, "public_key": None, "reason": "not_configured"}
    return {"available": True, "public_key": key}


@router.post("/notifications/push/subscriptions", status_code=201)
def register_web_push_subscription(
    payload: PushSubscriptionPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _scope(request, db)
    if not _vapid_configuration_ready():
        raise HTTPException(status_code=503, detail="Web Push delivery is not fully configured on this server")
    endpoint_hash = _endpoint_hash(payload.endpoint)
    subscription = db.query(CarePushSubscription).filter(
        CarePushSubscription.endpoint_hash == endpoint_hash
    ).first()
    if subscription is not None and (
        subscription.person_id != person_id or subscription.caregiver_id != caregiver_id
    ):
        raise HTTPException(status_code=409, detail="This device subscription is already linked to another care record")
    encrypted = _cipher().encrypt(
        json.dumps(payload.model_dump(), separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    now = datetime.now(timezone.utc)
    if subscription is None:
        subscription = CarePushSubscription(
            id=str(uuid.uuid4()),
            person_id=person_id,
            caregiver_id=caregiver_id,
            endpoint_hash=endpoint_hash,
            encrypted_subscription=encrypted,
            user_agent=(request.headers.get("user-agent") or "")[:255],
            created_at=now,
            last_seen_at=now,
        )
        db.add(subscription)
    else:
        subscription.encrypted_subscription = encrypted
        subscription.user_agent = (request.headers.get("user-agent") or "")[:255]
        subscription.last_seen_at = now
        subscription.revoked_at = None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This device subscription could not be registered; refresh and try again")
    return {"registered": True, "delivery_status": "subscription_saved_provider_delivery_pending"}


@router.delete("/notifications/push/subscriptions")
def revoke_web_push_subscription(
    payload: PushSubscriptionDelete,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, caregiver_id = _scope(request, db)
    subscription = db.query(CarePushSubscription).filter(
        CarePushSubscription.endpoint_hash == _endpoint_hash(payload.endpoint),
        CarePushSubscription.person_id == person_id,
        CarePushSubscription.caregiver_id == caregiver_id,
        CarePushSubscription.revoked_at.is_(None),
    ).first()
    if subscription is not None:
        subscription.revoked_at = datetime.now(timezone.utc)
        db.commit()
    return {"revoked": subscription is not None}
