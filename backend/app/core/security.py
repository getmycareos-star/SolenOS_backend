from __future__ import annotations

import os
import time
import threading
import uuid
import logging
import secrets
from collections import deque
from dataclasses import dataclass, field
from functools import wraps
from typing import Optional, List, Dict, Any, Callable

from fastapi import Depends, HTTPException, Request, status, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from app.core.database import get_db
from app.core.config import get_settings

JWT_ALGORITHM = "HS256"
from app.models.identity import Account, AccountPersonLink, Person, Session, AccountType
from sqlalchemy.orm import Session as DbSession
from sqlalchemy import and_
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

settings = get_settings()

security = HTTPBearer(auto_error=False)


class AuthenticatedPrincipal:
    "Represents an authenticated user with their authorized persons."

    def __init__(self, account: Account, authorized_person_ids: List[str]):
        self.account = account
        self.authorized_person_ids = authorized_person_ids

    @property
    def is_authenticated(self) -> bool:
        return self.account is not None and self.account.is_active

    def has_access_to_person(self, person_id: str) -> bool:
        "Check if the principal has access to the given person."
        return person_id in self.authorized_person_ids

    @property
    def is_operations(self) -> bool:
        "Check if the principal is a SolenOS team member (operations)."
        return self.account.account_type == AccountType.SOLENOS_STAFF


def get_current_user(
    request: Request,
    db: DbSession = Depends(get_db),
    token: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> AuthenticatedPrincipal:
    """Extract and validate the current user from the request.

    Looks for session token in:
    1. Authorization header (Bearer token)
    2. Session cookie

    Returns an AuthenticatedPrincipal with the account and list of authorized person IDs.
    """
    session_token = None

    # Try to get token from Authorization header
    if token and token.credentials:
        session_token = token.credentials
    else:
        # Try to get access token from cookie
        access_token = request.cookies.get("access_token")
        if access_token:
            try:
                # Decode JWT access token to get session ID
                payload = jwt.decode(access_token, settings.secret_key, algorithms=[JWT_ALGORITHM])
                session_id = payload.get("sid")
                if session_id:
                    # Look up session by ID instead of session_token
                    session = db.query(Session).filter(
                        and_(
                            Session.id == session_id,
                            Session.expires_at > datetime.now(timezone.utc),
                            Session.revoked_at.is_(None),
                        )
                    ).first()
                    if session:
                        # Use the actual session token for consistency with rest of function
                        session_token = session.session_token
            except jwt.PyJWTError:
                # Invalid token, will fall through to authentication error
                pass

    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Find the session in the database
    session = db.query(Session).filter(
        and_(
            Session.session_token == session_token,
            Session.expires_at > datetime.now(timezone.utc),
            Session.revoked_at.is_(None),
        )
    ).first()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Update last activity
    session.last_activity_at = datetime.now(timezone.utc)
    db.add(session)
    db.commit()

    # Get the account
    account = db.query(Account).filter(Account.id == session.account_id).first()
    if not account or not account.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get all active person links for this account
    person_links = db.query(AccountPersonLink).filter(
        and_(
            AccountPersonLink.account_id == account.id,
            AccountPersonLink.is_active == True,
            AccountPersonLink.effective_from <= datetime.now(timezone.utc),
            or_(
                AccountPersonLink.effective_until.is_(None),
                AccountPersonLink.effective_until >= datetime.now(timezone.utc),
            ),
        )
    ).all()

    authorized_person_ids = [link.person_id for link in person_links]

    if not authorized_person_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No authorized persons for this account",
        )

    return AuthenticatedPrincipal(account, authorized_person_ids)
def get_current_active_user(


    current_user: AuthenticatedPrincipal = Depends(get_current_user),
) -> AuthenticatedPrincipal:
    """Dependency for getting the current active user.

    This is a wrapper that ensures the user is active (though get_current_user already does this).
    """
    if not current_user.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not active",
        )
    return current_user


# Optional: A dependency that returns the first authorized person ID for backward compatibility
def get_current_person_id(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
) -> str:
    """Get the first authorized person ID for the current user.

    Note: This assumes the user is acting as a single person. For users authorized for
    multiple persons, the specific person should be specified in the request (e.g., in the path).
    """
    if not current_user.authorized_person_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No authorized persons for this user",
        )
    return current_user.authorized_person_ids[0]


# Optional: Alias for backward compatibility or naming preference
def get_current_principal(
    request: Request,
    db: DbSession = Depends(get_db),
    token: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> AuthenticatedPrincipal:
    "Alias for get_current_user - returns the authenticated principal."
    return get_current_user(request, db, token)


def get_principal_from_request(request: Request) -> AuthenticatedPrincipal:
    """Extract principal from request object (used in dependencies that don't have DB access).

    This is a simplified version for cases where we already have the request but need
    to extract the principal without DB dependencies in the signature.
    """
    # This would typically pull from request.state or similar
    # For now, we'll return a mock - in a real implementation this would
    # extract the principal that was already resolved by middleware
    raise NotImplementedError("get_principal_from_request requires implementation based on your auth setup")


def require_authenticated(principal: AuthenticatedPrincipal) -> None:
    "Raise an exception if the principal is not authenticated."
    if not principal.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )


def require_care_context_access(principal: AuthenticatedPrincipal, person_id: str, action: str = "") -> None:
    """Check if the principal has access to the specified person context.

    Args:
        principal: The authenticated principal
        person_id: The ID of the person to check access for
        action: Optional description of the action being performed (for logging)
    """
    if not principal.has_access_to_person(person_id):
        detail = f"Access denied to person {person_id}"
        if action:
            detail += f" for action: {action}"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


async def authorize_person_context_request(
    request: Request,
    principal: AuthenticatedPrincipal,
    db: DbSession,
    resource_models: List[tuple[str, Any]],
    action: str = "care context access",
) -> str:
    """Authorize the persisted owner of a care resource, not a caller-selected ID.

    If a request names a child resource, its database owner takes precedence over
    path, query, or body person IDs. Any disagreement is denied. This prevents
    a caller from attaching an authorized person_id to another person's resource.
    """
    require_authenticated(principal)

    payload: Dict[str, Any] = {}
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            body = await request.json()
            if isinstance(body, dict):
                payload = body
        except Exception:
            payload = {}

    nested_payloads = [
        value for key in ("input_data", "claim", "request", "payload", "revision")
        if isinstance((value := payload.get(key)), dict)
    ]
    supplied_contexts = [
        str(value) for value in (
            request.path_params.get("person_id"),
            request.query_params.get("person_id"),
            payload.get("person_id"),
            *(nested.get("person_id") for nested in nested_payloads),
        )
        if value not in (None, "")
    ]

    resource_owners: List[str] = []
    for parameter, model in resource_models:
        candidates = [
            source.get(parameter)
            for source in (request.path_params, request.query_params, payload, *nested_payloads)
            if source.get(parameter) not in (None, "")
        ]
        for resource_id in candidates:
            resource = db.query(model).filter(model.id == str(resource_id)).first()
            if resource is None:
                raise HTTPException(status_code=404, detail="Care resource not found")
            owner_id = getattr(resource, "person_id", None)
            if owner_id in (None, ""):
                raise HTTPException(status_code=404, detail="Care resource has no person context")
            resource_owners.append(str(owner_id))

    if resource_owners:
        if len(set(resource_owners)) != 1:
            raise HTTPException(status_code=403, detail="Resources belong to different care contexts")
        person_id = resource_owners[0]
        if any(value != person_id for value in supplied_contexts):
            raise HTTPException(status_code=403, detail="Care context does not match resource ownership")
    else:
        if not supplied_contexts:
            raise HTTPException(status_code=400, detail="A care context is required")
        person_id = supplied_contexts[0]
        if any(value != person_id for value in supplied_contexts):
            raise HTTPException(status_code=403, detail="Conflicting care contexts are not allowed")

    require_care_context_access(principal, person_id, action)
    return person_id


def require_team_access(principal: AuthenticatedPrincipal) -> AuthenticatedPrincipal:
    """Raise an exception if the principal is not authenticated or not a team member.

    Args:
        principal: The authenticated principal

    Returns:
        The principal if authenticated and a team member

    Raises:
        HTTPException: If not authenticated or not a team member
    """
    require_authenticated(principal)  # This will raise if not authenticated
    if not principal.is_operations:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Team access required",
        )
    return principal


def _log_security_event(event_type: str, request: Request, principal: AuthenticatedPrincipal, reason: str) -> None:
    """Log a security event.

    This is a simplified version for now. In a full implementation, this would
    send the event to a logging system or external service.
    """
    # For now, we'll just print the event to stdout for debugging.
    # In a real application, you would use a proper logging system.
    print(f'SECURITY EVENT: {event_type} - {reason} - Principal: {principal.account.id if principal.account else "None"}')


# MARKER_FOR_PASSWORD_FUNCTIONS_END

# Added password functions
# Password hashing functions
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    "Hash a password for secure storage."
    logger.debug("Password hashed")
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    "Verify a password against its hash."
    logger.debug("Password verification attempted")
    return pwd_context.verify(plain_password, hashed_password)


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Validate password strength.

    Returns:
        tuple: (is_valid, error_message)
    """
    if len(password) < 12:
        return False, "Password must be at least 12 characters"

    # Check for character variety
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)

    if not (has_upper and has_lower):
        return False, "Password must contain both uppercase and lowercase letters"

    if not has_digit:
        return False, "Password must contain at least one digit"

    if not has_special:
        return False, "Password must contain at least one special character"

    # Check for common weak patterns
    weak_passwords = ["password", "123456", "qwerty", "admin", "welcome"]
    password_lower = password.lower()
    for weak_pass in weak_passwords:
        if weak_pass in password_lower:
            return False, "Password contains weak pattern"

    return True, ""


def generate_api_key() -> str:
    "Generate a secure API key."
    return secrets.token_urlsafe(32)


def hash_and_validate_password(password: str) -> tuple[bool, str, str]:
    """Validate password strength and return hash if valid.

    Returns:
        tuple: (is_valid, error_message, hash)
        If is_valid is False, hash will be empty string
        If is_valid is True, hash will be the password hash
    """
    # First validate password strength
    is_valid, error_msg = validate_password_strength(password)
    if not is_valid:
        logger.warning(f"Password validation failed: {error_msg}")
        return False, error_msg, ""

    # If valid, hash the password
    try:
        password_hash = hash_password(password)
        logger.info("Password hashed successfully after validation")
        return True, "", password_hash
    except Exception as e:
        logger.error(f"Error hashing password: {e}")
        return False, "Error processing password", ""


def verify_password_secure(plain_password: str, stored_hash: str) -> bool:
    """Verify a password against its hash with logging of success/failure.

    Args:
        plain_password: The password to verify
        stored_hash: The hashed password to verify against

    Returns:
        bool: True if password matches hash, False otherwise
    """
    try:
        is_valid = verify_password(plain_password, stored_hash)
        if is_valid:
            logger.info("Password verification successful")
        else:
            logger.warning("Password verification failed - invalid credentials")
        return is_valid
    except Exception as e:
        logger.error(f"Error during password verification: {e}")
        return False


def add_security_headers(request: Request, response: Response) -> Response:
    """
    Add security headers to the response.
    """
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # HSTS only in production (non-debug) and if request is HTTPS
    if not settings.debug:
        # Check if request is secure (could be behind proxy)
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if proto == "https":
            # Consider adding preload in production if all subdomains are HTTPS
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # Permissions Policy
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    # Additional security headers for enhanced protection
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-DNS-Prefetch-Control"] = "off"
    response.headers["X-Download-Options"] = "noopen"
    # Expect-CT header (requires having a valid certificate and reporting setup)
    # Uncomment and configure if you have Certificate Transparency monitoring set up
    # response.headers["Expect-CT"] = "max-age=86400, enforce, report-uri=\"https://your-domain.com/ct-report\""
    # Content-Security-Policy
    # NOTE: This policy should be reviewed and hardened based on actual frontend requirements
    # The current policy allows 'unsafe-inline' for scripts and styles which may be necessary
    # for certain frontend frameworks but poses a security risk.
    # For production, consider:
    # 1. Using hashes or nonces for inline content instead of 'unsafe-inline'
    # 2. Specifying exact sources for scripts, styles, images, etc.
    # 3. Adding connect-src for API endpoints (e.g., connect-src 'self' https://api.example.com)
    # 4. Removing 'unsafe-inline' if frontend can be built without inline scripts/styles
    csp = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' https:; connect-src 'self'; frame-ancestors 'none';"
    response.headers["Content-Security-Policy"] = csp
    return response








