from fastapi import APIRouter, Depends, Query, Body, Request, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import (
    get_principal_from_request,
    require_authenticated,
)
from app.core.cache_decorators import cached_response


router = APIRouter(prefix="/user", tags=["user"])


class LanguageRequest(BaseModel):
    language: str
    ui_language: Optional[str] = None

    model_config = {"extra": "forbid"}


@cached_response(ttl=86400, key_prefix="user_language")
@router.get("/language")
def get_user_language(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get user language preference (identity derived from authenticated session)."""
    principal = require_authenticated(get_principal_from_request(request))
    return {
        "user_id": principal.account_id,
        "language": "en",
        "ui_language": "en",
    }


@router.post("/language")
def set_user_language(
    request: Request,
    language_request: LanguageRequest = Body(...),
    db: Session = Depends(get_db),
):
    """Set user language preference (identity derived from authenticated session)."""
    principal = require_authenticated(get_principal_from_request(request))
    return {
        "user_id": principal.account_id,
        "language": language_request.language,
        "ui_language": language_request.ui_language or language_request.language,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


class NotificationRequest(BaseModel):
    type: str
    enabled: bool
    channels: List[str] = ["push"]

    model_config = {"extra": "forbid"}


@cached_response(ttl=300, key_prefix="user_notifications")
@router.get("/notifications")
def get_notifications(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get user notification settings (identity derived from authenticated session)."""
    principal = require_authenticated(get_principal_from_request(request))
    return {
        "user_id": principal.account_id,
        "notifications": [],
    }


@router.post("/notifications")
def set_notifications(
    request: Request,
    notification_request: NotificationRequest = Body(...),
    db: Session = Depends(get_db),
):
    """Set notification preferences (identity derived from authenticated session)."""
    principal = require_authenticated(get_principal_from_request(request))
    return {
        "user_id": principal.account_id,
        "type": notification_request.type,
        "enabled": notification_request.enabled,
        "channels": notification_request.channels,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


class PrivacyRequest(BaseModel):
    data_types: List[str]
    consent: bool

    model_config = {"extra": "forbid"}


@cached_response(ttl=3600, key_prefix="user_privacy")
@router.get("/privacy")
def get_privacy(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get privacy settings (identity derived from authenticated session)."""
    principal = require_authenticated(get_principal_from_request(request))
    return {
        "user_id": principal.account_id,
        "data_sharing": {},
        "retention": "standard",
    }


@router.post("/privacy")
def set_privacy(
    request: Request,
    privacy_request: PrivacyRequest = Body(...),
    db: Session = Depends(get_db),
):
    """Set privacy preferences (identity derived from authenticated session)."""
    principal = require_authenticated(get_principal_from_request(request))
    return {
        "user_id": principal.account_id,
        "data_types": privacy_request.data_types,
        "consent": privacy_request.consent,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

