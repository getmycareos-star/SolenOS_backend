import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver
from app.models.caregiver_account import CaregiverAccount
from app.models.caregiver_access import CaregiverAccessGrant, CaregiverInvitation
from app.models.caregiver_intelligence import CaregiverProfile
from app.services.caregiver_profile_identity import ensure_caregiver_profile_for_session
from app.api.accounts import _hash_password, _validate_email

router = APIRouter(prefix="/caregiver-access", tags=["caregiver access"])
SESSION_TTL_DAYS = 365


class InvitationCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    expires_in_days: int = Field(default=7, ge=1, le=7)


class InvitationAccept(BaseModel):
    token: str = Field(min_length=32, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)
    name: str = Field(min_length=1, max_length=120)


def _actor_scope(request: Request) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not caregiver_id:
        raise HTTPException(status_code=401, detail="A valid care-record session is required")
    if not bool(getattr(request.state, "caregiver_can_manage_members", False)):
        raise HTTPException(status_code=403, detail="Only an authorised care-record administrator can manage access")
    return str(person_id), str(caregiver_id)


@router.get("/members")
def list_members(request: Request, db: Session = Depends(get_db)):
    person_id, _actor_id = _actor_scope(request)
    grants = (
        db.query(CaregiverAccessGrant, CaregiverProfile)
        .outerjoin(CaregiverProfile, CaregiverProfile.id == CaregiverAccessGrant.caregiver_profile_id)
        .filter(CaregiverAccessGrant.person_id == person_id)
        .order_by(CaregiverAccessGrant.granted_at.asc())
        .all()
    )
    return [{
        "caregiver_id": grant.caregiver_id,
        "caregiver_profile_id": grant.caregiver_profile_id,
        "name": profile.name if profile else "Caregiver",
        "email": profile.email if profile else None,
        "role": grant.role,
        "status": grant.status,
        "can_manage_members": bool(grant.can_manage_members),
        "granted_at": grant.granted_at.isoformat() if grant.granted_at else None,
        "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
    } for grant, profile in grants]


@router.post("/invitations", status_code=201)
def create_invitation(payload: InvitationCreate, request: Request, db: Session = Depends(get_db)):
    person_id, actor_id = _actor_scope(request)
    email = _validate_email(payload.email)
    if db.query(CaregiverAccessGrant).join(
        CaregiverProfile, CaregiverProfile.id == CaregiverAccessGrant.caregiver_profile_id
    ).filter(
        CaregiverAccessGrant.person_id == person_id,
        CaregiverAccessGrant.status == "active",
        CaregiverProfile.email == email,
    ).first():
        raise HTTPException(status_code=409, detail="This email already has access to the care record.")
    pending = db.query(CaregiverInvitation).filter(
        CaregiverInvitation.person_id == person_id,
        CaregiverInvitation.email_normalized == email,
        CaregiverInvitation.status == "pending",
        CaregiverInvitation.expires_at > datetime.now(timezone.utc),
    ).first()
    if pending:
        raise HTTPException(status_code=409, detail="An active invitation already exists for this email address.")

    raw_token = secrets.token_urlsafe(32)
    invitation = CaregiverInvitation(
        person_id=person_id,
        email_normalized=email,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        invited_by_caregiver_id=actor_id,
        role="family",
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days),
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    # The caller must deliver this secret through a trusted channel. SolenOS
    # does not claim that an email was sent; no delivery provider is invoked here.
    return {
        "invitation_id": invitation.id,
        "email": invitation.email_normalized,
        "status": invitation.status,
        "expires_at": invitation.expires_at.isoformat(),
        "invitation_token": raw_token,
        "delivery_status": "not_sent",
    }


@router.post("/invitations/accept", status_code=201)
def accept_invitation(payload: InvitationAccept, db: Session = Depends(get_db)):
    email = _validate_email(payload.email)
    token_hash = hashlib.sha256(payload.token.strip().encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    invitation = db.query(CaregiverInvitation).filter(
        CaregiverInvitation.token_hash == token_hash,
        CaregiverInvitation.status == "pending",
        CaregiverInvitation.revoked_at.is_(None),
    ).with_for_update().first()
    if invitation is None:
        raise HTTPException(status_code=404, detail="This invitation is invalid, revoked or already used.")
    expiry = invitation.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= now:
        invitation.status = "expired"
        db.commit()
        raise HTTPException(status_code=410, detail="This invitation has expired.")
    if not secrets.compare_digest(invitation.email_normalized, email):
        raise HTTPException(status_code=403, detail="This invitation was issued to a different email address.")
    if db.query(CaregiverAccount).filter(CaregiverAccount.email_normalized == email).first():
        raise HTTPException(status_code=409, detail="An account already uses this email address.")
    if db.query(Caregiver).filter(Caregiver.email == email).first():
        raise HTTPException(status_code=409, detail="This email is already linked to a caregiver identity.")

    caregiver = Caregiver(
        name=payload.name.strip(),
        email=email,
        relationship="Family",
        person_id=invitation.person_id,
    )
    db.add(caregiver)
    db.flush()
    profile = ensure_caregiver_profile_for_session(
        db,
        person_id=invitation.person_id,
        session_caregiver_id=caregiver.id,
        name=caregiver.name,
        relationship=caregiver.relationship,
        email=email,
    )
    account = CaregiverAccount(
        email_normalized=email,
        password_hash=_hash_password(payload.password),
        person_id=invitation.person_id,
        caregiver_id=caregiver.id,
    )
    grant = CaregiverAccessGrant(
        person_id=invitation.person_id,
        caregiver_id=caregiver.id,
        caregiver_profile_id=profile.id,
        role=invitation.role,
        status="active",
        can_manage_members=False,
        granted_by_caregiver_id=invitation.invited_by_caregiver_id,
    )
    raw_session_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(days=SESSION_TTL_DAYS)
    session = AnonymousSession(
        person_id=invitation.person_id,
        caregiver_id=caregiver.id,
        account_id=account.id,
        token_hash=hashlib.sha256(raw_session_token.encode("utf-8")).hexdigest(),
        expires_at=expires_at,
    )
    invitation.status = "accepted"
    invitation.accepted_at = now
    invitation.accepted_caregiver_id = caregiver.id
    db.add_all([account, grant, session])
    db.commit()
    return {
        "accepted": True,
        "person_id": invitation.person_id,
        "caregiver_id": caregiver.id,
        "caregiver_profile_id": profile.id,
        "account": {"email": email},
        "session_id": session.id,
        "access_token": raw_session_token,
        "token_type": "Bearer",
        "expires_at": expires_at.isoformat(),
        "access_scope": "family_care_record",
    }


@router.delete("/invitations/{invitation_id}")
def revoke_invitation(invitation_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _actor_id = _actor_scope(request)
    invitation = db.query(CaregiverInvitation).filter(
        CaregiverInvitation.id == invitation_id,
        CaregiverInvitation.person_id == person_id,
        CaregiverInvitation.status == "pending",
    ).first()
    if invitation is None:
        raise HTTPException(status_code=404, detail="Pending invitation not found.")
    invitation.status = "revoked"
    invitation.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return {"revoked": True, "invitation_id": invitation.id}


@router.delete("/members/{caregiver_id}")
def revoke_member_access(caregiver_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, actor_id = _actor_scope(request)
    if str(caregiver_id) == actor_id:
        raise HTTPException(status_code=409, detail="An administrator cannot revoke their own access through this operation.")
    grant = db.query(CaregiverAccessGrant).filter(
        CaregiverAccessGrant.person_id == person_id,
        CaregiverAccessGrant.caregiver_id == caregiver_id,
        CaregiverAccessGrant.status == "active",
    ).with_for_update().first()
    if grant is None:
        raise HTTPException(status_code=404, detail="Active caregiver access was not found.")
    now = datetime.now(timezone.utc)
    grant.status = "revoked"
    grant.revoked_at = now
    db.query(AnonymousSession).filter(
        AnonymousSession.person_id == person_id,
        AnonymousSession.caregiver_id == caregiver_id,
        AnonymousSession.revoked_at.is_(None),
    ).update({AnonymousSession.revoked_at: now}, synchronize_session=False)
    db.commit()
    return {"revoked": True, "caregiver_id": caregiver_id, "sessions_revoked": True}
