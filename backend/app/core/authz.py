"""Fail-closed authorization helpers for sensitive care-record routes."""
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.authentication import current_account
from app.core.database import get_db
from app.models.auth_identity import AccountPersonAccess, AuthAccount


def require_person_access(permission: str = "read"):
    """Require an explicit active grant for the route's {person_id} path parameter.

    Use only on endpoints whose route declares a path parameter named person_id.
    """
    def dependency(
        person_id: str,
        account: AuthAccount = Depends(current_account),
        db: Session = Depends(get_db),
    ) -> AuthAccount:
        grant = (
            db.query(AccountPersonAccess)
            .filter(
                AccountPersonAccess.account_id == account.id,
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
        return account
    return dependency
