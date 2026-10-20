"""Fail-closed authorization helpers for sensitive care-record routes."""
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.authentication import current_account
from app.core.database import get_db
from app.models.auth_identity import AccountPersonAccess, AuthAccount


def assert_person_access(db: Session, account_id: str, person_id: str, permission: str = "read") -> AccountPersonAccess:
    """Enforce an explicit active grant and the requested permission."""
    grant = (
        db.query(AccountPersonAccess)
        .filter(
            AccountPersonAccess.account_id == account_id,
            AccountPersonAccess.person_id == person_id,
            AccountPersonAccess.is_active.is_(True),
            AccountPersonAccess.revoked_at.is_(None),
        )
        .first()
    )
    if grant is None:
        raise HTTPException(status_code=403, detail="No access to this care record")
    allowed = grant.permissions or []
    if permission not in allowed and "admin" not in allowed:
        raise HTTPException(status_code=403, detail="Permission not granted for this care record")
    return grant


def require_person_access(permission: str = "read"):
    """Require an explicit grant for the route's {person_id} path parameter.

    Use only on endpoints whose route declares a path parameter named person_id.
    """
    def dependency(
        person_id: str,
        account: AuthAccount = Depends(current_account),
        db: Session = Depends(get_db),
    ) -> AuthAccount:
        assert_person_access(db, account.id, person_id, permission)
        return account
    return dependency



def require_staff(account: AuthAccount = Depends(current_account)) -> AuthAccount:
    """Deny system-wide administrative actions unless staff was granted out of band."""
    if not account.is_staff:
        raise HTTPException(status_code=403, detail="Staff access required")
    return account
