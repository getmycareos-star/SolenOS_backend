"""Fail-closed compatibility endpoints for the in-app-only notification policy."""
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.collaboration import _scope
from app.core.database import get_db
from app.models.notifications import CarePushSubscription

router = APIRouter(tags=["notifications"])


class PushSubscriptionDelete(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2048)


def _endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


@router.get("/notifications/push/public-key")
def get_web_push_public_key(request: Request, db: Session = Depends(get_db)):
    _scope(request, db)
    return {"available": False, "public_key": None, "reason": "in_app_only"}


@router.post("/notifications/push/subscriptions", status_code=410)
def register_web_push_subscription(request: Request, db: Session = Depends(get_db)):
    _scope(request, db)
    raise HTTPException(
        status_code=410,
        detail="Browser alerts are not available. Notifications stay in the SolenOS inbox.",
    )


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
