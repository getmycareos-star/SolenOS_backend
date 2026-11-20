"""Server-side caregiver operation permissions."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.caregiver_access import CaregiverAccessGrant

CAREGIVER_PERMISSION_KEYS = frozenset({
    "view_lcr", "add_evidence", "correct_records", "manage_responsibilities",
    "manage_reminders", "complete_assigned_reminders", "manage_handoffs",
    "share_summaries", "share_observations", "view_documents", "view_communications",
})
DEFAULT_FAMILY_PERMISSIONS = frozenset({
    "view_lcr", "add_evidence", "manage_responsibilities", "manage_reminders",
    "complete_assigned_reminders", "manage_handoffs", "share_summaries",
    "share_observations", "view_documents", "view_communications",
})


def caregiver_has_permission(grant: CaregiverAccessGrant | None, permission: str) -> bool:
    if grant is None or permission not in CAREGIVER_PERMISSION_KEYS:
        return False
    if grant.status != "active" or grant.revoked_at is not None:
        return False
    permissions = grant.permissions or []
    if grant.access_scope == "documents_only":
        return permission == "view_documents" and permission in set(permissions)
    if grant.access_scope != "full_record":
        return False
    if bool(grant.can_manage_members):
        return True
    return permission in set(permissions)


def require_caregiver_permission(db: Session, person_id: str, caregiver_id: str, permission: str) -> CaregiverAccessGrant:
    grant = db.query(CaregiverAccessGrant).filter(
        CaregiverAccessGrant.person_id == str(person_id),
        CaregiverAccessGrant.caregiver_id == str(caregiver_id),
        CaregiverAccessGrant.status == "active",
        CaregiverAccessGrant.revoked_at.is_(None),
    ).first()
    if not caregiver_has_permission(grant, permission):
        raise HTTPException(status_code=403, detail=f"This caregiver does not have permission to {permission.replace('_', ' ')}.")
    return grant


def require_request_caregiver_permission(request, db: Session, permission: str) -> CaregiverAccessGrant:
    """Enforce an operation permission using the bearer-scoped request identity."""
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not caregiver_id:
        raise HTTPException(status_code=401, detail="A verified caregiver session is required.")
    return require_caregiver_permission(db, str(person_id), str(caregiver_id), permission)
