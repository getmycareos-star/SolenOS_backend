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
from app.models.caregiver_access import CaregiverAccessGrant, CaregiverInvitation, CaregiverResponsibilityTransfer, CaregiverPrimaryTransfer
from app.models.caregiver_intelligence import CaregiverProfile, CaregiverResponsibility
from app.models.open_loop import OpenLoopRecord, OpenLoopEvent
from app.models.privacy_delivery import AuditLogEvent
from app.core.caregiver_enums import ResponsibilityAcceptanceStatus, ResponsibilityStatus
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

class AccessScopeUpdate(BaseModel):
    access_scope: str = Field(pattern="^(full_record|documents_only)$")


def _audit_event(actor_id: str | None, person_id: str, action: str, target_type: str, target_id: str, metadata: dict | None = None):
    return AuditLogEvent(
        actor_id=actor_id or "unknown_actor",
        subject_id=person_id,
        person_id=person_id,
        caregiver_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        occurred_at=datetime.now(timezone.utc),
        outcome="success",
        request_id=secrets.token_hex(16),
        policy_version="caregiver-access-v1",
        record_metadata=metadata or {},
    )


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
        "access_scope": grant.access_scope or "full_record",
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
    db.flush()
    db.add(_audit_event(actor_id, person_id, "caregiver.invitation.created", "caregiver_invitation", invitation.id, {"recipient_email": email, "expires_at": invitation.expires_at.isoformat()}))
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
    db.add(account)
    db.flush()  # Assign the account primary key before linking the new session.
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
    db.add_all([grant, session])
    db.add(_audit_event(caregiver.id, invitation.person_id, "caregiver.invitation.accepted", "caregiver_invitation", invitation.id, {"caregiver_id": caregiver.id, "role": invitation.role}))
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
    ).with_for_update().first()
    if invitation is None:
        raise HTTPException(status_code=404, detail="Pending invitation not found.")
    invitation.status = "revoked"
    invitation.revoked_at = datetime.now(timezone.utc)
    db.add(_audit_event(_actor_id, person_id, "caregiver.invitation.revoked", "caregiver_invitation", invitation.id))
    db.commit()
    return {"revoked": True, "invitation_id": invitation.id}



@router.patch("/members/{caregiver_id}/access-scope")
def update_member_access_scope(
    caregiver_id: str,
    payload: AccessScopeUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, actor_id = _actor_scope(request)
    grant = db.query(CaregiverAccessGrant).filter(
        CaregiverAccessGrant.person_id == person_id,
        CaregiverAccessGrant.caregiver_id == caregiver_id,
        CaregiverAccessGrant.status == "active",
        CaregiverAccessGrant.revoked_at.is_(None),
    ).with_for_update().first()
    if grant is None:
        raise HTTPException(status_code=404, detail="Active caregiver access was not found.")
    if grant.role == "primary" and payload.access_scope != "full_record":
        raise HTTPException(status_code=409, detail="The primary caregiver must retain full care-record access.")
    grant.access_scope = payload.access_scope
    grant.updated_at = datetime.now(timezone.utc)
    db.add(_audit_event(actor_id, person_id, "caregiver.access_scope.updated", "caregiver_access_grant", grant.id, {
        "caregiver_id": caregiver_id,
        "access_scope": payload.access_scope,
    }))
    db.commit()
    return {"updated": True, "caregiver_id": caregiver_id, "access_scope": grant.access_scope}

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
    db.add(_audit_event(actor_id, person_id, "caregiver.access.revoked", "caregiver_access_grant", grant.id, {"caregiver_id": caregiver_id, "sessions_revoked": True}))
    db.commit()
    return {"revoked": True, "caregiver_id": caregiver_id, "sessions_revoked": True}


class ResponsibilityTransferCreate(BaseModel):
    source_responsibility_id: str = Field(min_length=1, max_length=160)
    to_caregiver_profile_id: str = Field(min_length=1, max_length=160)
    related_record_type: str | None = Field(default=None, max_length=40)
    related_record_id: str | None = Field(default=None, max_length=160)
    reason: str | None = Field(default=None, max_length=1000)


def _current_profile(request: Request, db: Session) -> tuple[str, str, CaregiverProfile]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not caregiver_id:
        raise HTTPException(status_code=401, detail="A valid care-record session is required")
    try:
        profile = ensure_caregiver_profile_for_session(
            db, person_id=str(person_id), session_caregiver_id=str(caregiver_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Caregiver identity is not linked to this care record") from exc
    return str(person_id), str(caregiver_id), profile


@router.post("/responsibility-transfers", status_code=201)
def create_responsibility_transfer(
    payload: ResponsibilityTransferCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, actor_id = _actor_scope(request)
    source = db.query(CaregiverResponsibility).filter(
        CaregiverResponsibility.id == payload.source_responsibility_id,
        CaregiverResponsibility.person_id == person_id,
    ).with_for_update().first()
    if source is None:
        raise HTTPException(status_code=404, detail="The current responsibility was not found.")
    if getattr(source.status, "value", source.status) in {"historical", "gap"}:
        raise HTTPException(status_code=409, detail="This responsibility is no longer active.")
    target = db.query(CaregiverProfile).filter(
        CaregiverProfile.id == payload.to_caregiver_profile_id,
        CaregiverProfile.person_id == person_id,
    ).first()
    if target is None or not target.session_caregiver_id:
        raise HTTPException(status_code=404, detail="The invited caregiver identity was not found.")
    target_grant = db.query(CaregiverAccessGrant).filter(
        CaregiverAccessGrant.person_id == person_id,
        CaregiverAccessGrant.caregiver_id == target.session_caregiver_id,
        CaregiverAccessGrant.status == "active",
        CaregiverAccessGrant.revoked_at.is_(None),
    ).first()
    if target_grant is None:
        raise HTTPException(status_code=403, detail="The receiving caregiver is not authorised for this care record.")
    if str(source.caregiver_id) == str(target.id):
        raise HTTPException(status_code=409, detail="This responsibility is already assigned to that caregiver.")
    from_profile = db.query(CaregiverProfile).filter(
        CaregiverProfile.id == source.caregiver_id,
        CaregiverProfile.person_id == person_id,
    ).first()
    linked_open_loop = None
    if payload.related_record_type == "open_loop":
        if not payload.related_record_id:
            raise HTTPException(status_code=422, detail="An open-loop transfer requires the existing follow-up ID.")
        linked_open_loop = db.query(OpenLoopRecord).filter(
            OpenLoopRecord.id == payload.related_record_id,
            OpenLoopRecord.person_id == person_id,
        ).with_for_update().first()
        if linked_open_loop is None or from_profile is None or not from_profile.session_caregiver_id:
            raise HTTPException(status_code=404, detail="The existing follow-up or its assigned caregiver was not found.")
        current_assignment = linked_open_loop.assigned_caregiver_id or linked_open_loop.caregiver_id
        if str(current_assignment) != str(from_profile.session_caregiver_id):
            raise HTTPException(status_code=409, detail="The follow-up is no longer assigned to the caregiver named in this transfer.")
    pending = db.query(CaregiverResponsibilityTransfer).filter(
        CaregiverResponsibilityTransfer.person_id == person_id,
        CaregiverResponsibilityTransfer.source_responsibility_id == source.id,
        CaregiverResponsibilityTransfer.status == "pending",
    ).first()
    if pending:
        raise HTTPException(status_code=409, detail="A transfer for this responsibility is already awaiting a response.")

    transfer = CaregiverResponsibilityTransfer(
        person_id=person_id,
        source_responsibility_id=source.id,
        from_caregiver_profile_id=source.caregiver_id,
        to_caregiver_profile_id=target.id,
        created_by_caregiver_id=actor_id,
        related_record_type=payload.related_record_type,
        related_record_id=payload.related_record_id,
        reason=payload.reason,
        status="pending",
    )
    db.add(transfer)
    db.flush()
    db.add(_audit_event(actor_id, person_id, "caregiver.responsibility_transfer.requested", "caregiver_responsibility_transfer", transfer.id, {"to_caregiver_profile_id": target.id, "responsibility_id": source.id}))
    db.commit()
    db.refresh(transfer)
    return {
        "id": transfer.id,
        "status": transfer.status,
        "responsibility_type": source.responsibility_type,
        "from_caregiver_profile_id": transfer.from_caregiver_profile_id,
        "to_caregiver_profile_id": transfer.to_caregiver_profile_id,
        "related_record_type": transfer.related_record_type,
        "related_record_id": transfer.related_record_id,
        "created_at": transfer.created_at.isoformat() if transfer.created_at else None,
    }


@router.get("/responsibility-transfers")
def list_responsibility_transfers(request: Request, db: Session = Depends(get_db)):
    person_id, _actor_id, profile = _current_profile(request, db)
    query = db.query(CaregiverResponsibilityTransfer).filter(
        CaregiverResponsibilityTransfer.person_id == person_id
    )
    if not bool(getattr(request.state, "caregiver_can_manage_members", False)):
        query = query.filter(CaregiverResponsibilityTransfer.to_caregiver_profile_id == profile.id)
    transfers = query.order_by(CaregiverResponsibilityTransfer.created_at.desc()).all()
    result = []
    for item in transfers:
        source = db.query(CaregiverResponsibility).filter(
            CaregiverResponsibility.id == item.source_responsibility_id,
            CaregiverResponsibility.person_id == person_id,
        ).first()
        from_profile = db.query(CaregiverProfile).filter(CaregiverProfile.id == item.from_caregiver_profile_id).first()
        to_profile = db.query(CaregiverProfile).filter(CaregiverProfile.id == item.to_caregiver_profile_id).first()
        result.append({
            "id": item.id,
            "source_responsibility_id": item.source_responsibility_id,
            "responsibility_type": source.responsibility_type if source else "Care responsibility",
            "from_caregiver_profile_id": item.from_caregiver_profile_id,
            "from_caregiver_id": from_profile.session_caregiver_id if from_profile else None,
            "from_caregiver_name": from_profile.name if from_profile else "Previous caregiver",
            "to_caregiver_profile_id": item.to_caregiver_profile_id,
            "to_caregiver_id": to_profile.session_caregiver_id if to_profile else None,
            "to_caregiver_name": to_profile.name if to_profile else "Caregiver",
            "related_record_type": item.related_record_type,
            "related_record_id": item.related_record_id,
            "reason": item.reason,
            "status": item.status,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
        })
    return result


@router.post("/responsibility-transfers/{transfer_id}/accept")
def accept_responsibility_transfer(transfer_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, profile = _current_profile(request, db)
    transfer = db.query(CaregiverResponsibilityTransfer).filter(
        CaregiverResponsibilityTransfer.id == transfer_id,
        CaregiverResponsibilityTransfer.person_id == person_id,
        CaregiverResponsibilityTransfer.to_caregiver_profile_id == profile.id,
        CaregiverResponsibilityTransfer.status == "pending",
    ).with_for_update().first()
    if transfer is None:
        raise HTTPException(status_code=404, detail="Pending responsibility transfer not found.")
    source = db.query(CaregiverResponsibility).filter(
        CaregiverResponsibility.id == transfer.source_responsibility_id,
        CaregiverResponsibility.person_id == person_id,
    ).with_for_update().first()
    if source is None or str(source.caregiver_id) != str(transfer.from_caregiver_profile_id) or getattr(source.status, "value", source.status) in {"historical", "gap"}:
        transfer.status = "stale"
        db.commit()
        raise HTTPException(status_code=409, detail="The original responsibility changed before this transfer was accepted.")
    now = datetime.now(timezone.utc)
    linked_open_loop = None
    if transfer.related_record_type == "open_loop":
        linked_open_loop = db.query(OpenLoopRecord).filter(
            OpenLoopRecord.id == transfer.related_record_id,
            OpenLoopRecord.person_id == person_id,
        ).with_for_update().first()
        from_profile = db.query(CaregiverProfile).filter(
            CaregiverProfile.id == transfer.from_caregiver_profile_id,
            CaregiverProfile.person_id == person_id,
        ).first()
        if (
            linked_open_loop is None
            or from_profile is None
            or not from_profile.session_caregiver_id
            or not profile.session_caregiver_id
            or str(linked_open_loop.assigned_caregiver_id or linked_open_loop.caregiver_id) != str(from_profile.session_caregiver_id)
        ):
            transfer.status = "stale"
            db.commit()
            raise HTTPException(status_code=409, detail="The linked follow-up changed before this transfer was accepted.")
        before_state = {
            "assigned_caregiver_id": linked_open_loop.assigned_caregiver_id,
            "creator_caregiver_id": linked_open_loop.caregiver_id,
        }
        linked_open_loop.assigned_caregiver_id = profile.session_caregiver_id
        linked_open_loop.owner_label = profile.name
        linked_open_loop.updated_at = now
        linked_open_loop.version = int(linked_open_loop.version or 0) + 1
        db.add(OpenLoopEvent(
            loop_id=linked_open_loop.id,
            person_id=person_id,
            caregiver_id=session_caregiver_id,
            event_type="responsibility_transferred",
            before_state=before_state,
            after_state={
                "assigned_caregiver_id": linked_open_loop.assigned_caregiver_id,
                "creator_caregiver_id": linked_open_loop.caregiver_id,
                "transfer_id": transfer.id,
            },
            evidence=[{"type": "caregiver_responsibility_transfer", "transfer_id": transfer.id}],
            actor=session_caregiver_id,
        ))
    source.status = ResponsibilityStatus.HISTORICAL
    source.effective_end = now
    replacement = CaregiverResponsibility(
        person_id=person_id,
        caregiver_id=profile.id,
        responsibility_type=source.responsibility_type,
        scope=source.scope,
        effective_start=now,
        effective_end=None,
        status=ResponsibilityStatus.ACTIVE,
        acceptance_status=ResponsibilityAcceptanceStatus.EXPLICIT,
        assignment_source="accepted_caregiver_transfer",
        is_joint=False,
        joint_with_caregiver_id=None,
        schedule_pattern=source.schedule_pattern,
        provenance=source.provenance,
    )
    transfer.status = "accepted"
    transfer.accepted_at = now
    transfer.accepted_by_caregiver_id = session_caregiver_id
    db.add(replacement)
    db.add(_audit_event(session_caregiver_id, person_id, "caregiver.responsibility_transfer.accepted", "caregiver_responsibility_transfer", transfer.id, {"new_responsibility_id": replacement.id, "previous_responsibility_id": source.id}))
    db.commit()
    db.refresh(replacement)
    return {
        "accepted": True,
        "transfer_id": transfer.id,
        "previous_responsibility_id": source.id,
        "new_responsibility_id": replacement.id,
        "responsibility_type": replacement.responsibility_type,
        "related_record_type": transfer.related_record_type,
        "related_record_id": transfer.related_record_id,
    }


@router.post("/responsibility-transfers/{transfer_id}/reject")
def reject_responsibility_transfer(transfer_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _session_caregiver_id, profile = _current_profile(request, db)
    transfer = db.query(CaregiverResponsibilityTransfer).filter(
        CaregiverResponsibilityTransfer.id == transfer_id,
        CaregiverResponsibilityTransfer.person_id == person_id,
        CaregiverResponsibilityTransfer.to_caregiver_profile_id == profile.id,
        CaregiverResponsibilityTransfer.status == "pending",
    ).with_for_update().first()
    if transfer is None:
        raise HTTPException(status_code=404, detail="Pending responsibility transfer not found.")
    transfer.status = "rejected"
    db.add(_audit_event(_session_caregiver_id, person_id, "caregiver.responsibility_transfer.rejected", "caregiver_responsibility_transfer", transfer.id))
    db.commit()
    return {"rejected": True, "transfer_id": transfer.id}


class PrimaryTransferCreate(BaseModel):
    to_caregiver_id: str = Field(min_length=1, max_length=160)
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/primary-transfers", status_code=201)
def create_primary_transfer(payload: PrimaryTransferCreate, request: Request, db: Session = Depends(get_db)):
    person_id, actor_id = _actor_scope(request)
    current_grant = db.query(CaregiverAccessGrant).filter(
        CaregiverAccessGrant.person_id == person_id,
        CaregiverAccessGrant.caregiver_id == actor_id,
        CaregiverAccessGrant.status == "active",
        CaregiverAccessGrant.role == "primary",
        CaregiverAccessGrant.can_manage_members.is_(True),
    ).with_for_update().first()
    if current_grant is None:
        raise HTTPException(status_code=403, detail="Only the current primary caregiver can transfer primary authority.")
    target_grant = db.query(CaregiverAccessGrant).filter(
        CaregiverAccessGrant.person_id == person_id,
        CaregiverAccessGrant.caregiver_id == payload.to_caregiver_id,
        CaregiverAccessGrant.status == "active",
        CaregiverAccessGrant.revoked_at.is_(None),
    ).first()
    if target_grant is None or str(target_grant.caregiver_id) == actor_id:
        raise HTTPException(status_code=404, detail="The receiving caregiver does not have active access.")
    pending = db.query(CaregiverPrimaryTransfer).filter(
        CaregiverPrimaryTransfer.person_id == person_id,
        CaregiverPrimaryTransfer.status == "pending",
    ).first()
    if pending:
        raise HTTPException(status_code=409, detail="A primary caregiver transfer is already awaiting acceptance.")
    transfer = CaregiverPrimaryTransfer(
        person_id=person_id,
        from_caregiver_id=actor_id,
        to_caregiver_id=target_grant.caregiver_id,
        reason=payload.reason,
        status="pending",
    )
    db.add(transfer)
    db.flush()
    db.add(_audit_event(actor_id, person_id, "caregiver.primary_transfer.requested", "caregiver_primary_transfer", transfer.id, {"to_caregiver_id": transfer.to_caregiver_id}))
    db.commit()
    db.refresh(transfer)
    return {"id": transfer.id, "status": transfer.status, "from_caregiver_id": transfer.from_caregiver_id,
            "to_caregiver_id": transfer.to_caregiver_id, "reason": transfer.reason,
            "created_at": transfer.created_at.isoformat() if transfer.created_at else None}


@router.get("/primary-transfers")
def list_primary_transfers(request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, _profile = _current_profile(request, db)
    query = db.query(CaregiverPrimaryTransfer).filter(CaregiverPrimaryTransfer.person_id == person_id)
    if not bool(getattr(request.state, "caregiver_can_manage_members", False)):
        query = query.filter(CaregiverPrimaryTransfer.to_caregiver_id == session_caregiver_id)
    result = []
    for item in query.order_by(CaregiverPrimaryTransfer.created_at.desc()).all():
        from_profile = db.query(CaregiverProfile).filter(
            CaregiverProfile.person_id == person_id,
            CaregiverProfile.session_caregiver_id == item.from_caregiver_id,
        ).first()
        to_profile = db.query(CaregiverProfile).filter(
            CaregiverProfile.person_id == person_id,
            CaregiverProfile.session_caregiver_id == item.to_caregiver_id,
        ).first()
        result.append({
            "id": item.id,
            "from_caregiver_id": item.from_caregiver_id,
            "from_caregiver_name": from_profile.name if from_profile else "Previous caregiver",
            "to_caregiver_id": item.to_caregiver_id,
            "to_caregiver_name": to_profile.name if to_profile else "Caregiver",
            "reason": item.reason,
            "status": item.status,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
        })
    return result


@router.post("/primary-transfers/{transfer_id}/accept")
def accept_primary_transfer(transfer_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, _profile = _current_profile(request, db)
    transfer = db.query(CaregiverPrimaryTransfer).filter(
        CaregiverPrimaryTransfer.id == transfer_id,
        CaregiverPrimaryTransfer.person_id == person_id,
        CaregiverPrimaryTransfer.to_caregiver_id == session_caregiver_id,
        CaregiverPrimaryTransfer.status == "pending",
    ).with_for_update().first()
    if transfer is None:
        raise HTTPException(status_code=404, detail="Pending primary caregiver transfer not found.")
    old_grant = db.query(CaregiverAccessGrant).filter(
        CaregiverAccessGrant.person_id == person_id,
        CaregiverAccessGrant.caregiver_id == transfer.from_caregiver_id,
        CaregiverAccessGrant.status == "active",
        CaregiverAccessGrant.role == "primary",
        CaregiverAccessGrant.can_manage_members.is_(True),
    ).with_for_update().first()
    new_grant = db.query(CaregiverAccessGrant).filter(
        CaregiverAccessGrant.person_id == person_id,
        CaregiverAccessGrant.caregiver_id == session_caregiver_id,
        CaregiverAccessGrant.status == "active",
        CaregiverAccessGrant.revoked_at.is_(None),
    ).with_for_update().first()
    if old_grant is None or new_grant is None:
        transfer.status = "stale"
        db.commit()
        raise HTTPException(status_code=409, detail="The primary caregiver authority changed before this transfer was accepted.")
    now = datetime.now(timezone.utc)
    old_grant.role = "family"
    old_grant.can_manage_members = False
    old_grant.updated_at = now
    new_grant.role = "primary"
    new_grant.access_scope = "full_record"
    new_grant.can_manage_members = True
    new_grant.updated_at = now
    transfer.status = "accepted"
    transfer.accepted_at = now
    transfer.accepted_by_caregiver_id = session_caregiver_id
    db.add(_audit_event(session_caregiver_id, person_id, "caregiver.primary_transfer.accepted", "caregiver_primary_transfer", transfer.id, {"previous_primary_caregiver_id": transfer.from_caregiver_id}))
    db.commit()
    return {"accepted": True, "transfer_id": transfer.id, "new_primary_caregiver_id": session_caregiver_id,
            "previous_primary_caregiver_id": transfer.from_caregiver_id,
            "previous_caregiver_access_retained": old_grant.status == "active"}


@router.post("/primary-transfers/{transfer_id}/reject")
def reject_primary_transfer(transfer_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, session_caregiver_id, _profile = _current_profile(request, db)
    transfer = db.query(CaregiverPrimaryTransfer).filter(
        CaregiverPrimaryTransfer.id == transfer_id,
        CaregiverPrimaryTransfer.person_id == person_id,
        CaregiverPrimaryTransfer.to_caregiver_id == session_caregiver_id,
        CaregiverPrimaryTransfer.status == "pending",
    ).with_for_update().first()
    if transfer is None:
        raise HTTPException(status_code=404, detail="Pending primary caregiver transfer not found.")
    transfer.status = "rejected"
    db.add(_audit_event(session_caregiver_id, person_id, "caregiver.primary_transfer.rejected", "caregiver_primary_transfer", transfer.id))
    db.commit()
    return {"rejected": True, "transfer_id": transfer.id}
