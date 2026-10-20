"""Authentication service for SolenOS.

Implements secure authentication with:
- Argon2id password hashing
- JWT access/refresh tokens
- Session management with rotation
- MFA support (TOTP)
- Account lockout protection
- Anonymous session support
"""

import secrets
import hashlib
import base64
import binascii
import hmac
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, Any, List
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from sqlalchemy.orm import Session

password_hasher = PasswordHasher()

from app.core.config import get_settings
from app.core.identity_enums import AccountType, SessionType, DeviceTrustLevel, AuthProvider
from app.models.identity import (
    Account, Session, Device, AnonymousSessionRecord,
    ProfessionalProfile, StaffProfile
)
from app.models.caregiver_intelligence import CaregiverProfile
from app.services.alerting import send_security_alert


settings = get_settings()


# JWT Configuration
JWT_ALGORITHM = settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.jwt_access_token_expire_minutes
REFRESH_TOKEN_EXPIRE_DAYS = settings.jwt_refresh_token_expire_days
ANONYMOUS_SESSION_EXPIRE_DAYS = settings.jwt_anonymous_session_expire_days


class AuthenticationError(Exception):
    """Authentication-related errors."""
    pass


class AccountLockedError(AuthenticationError):
    """Account is temporarily locked."""
    def __init__(self, locked_until: datetime):
        self.locked_until = locked_until
        super().__init__(f"Account locked until {locked_until.isoformat()}")


class InvalidCredentialsError(AuthenticationError):
    """Invalid email or password."""
    pass


class MFARequiredError(AuthenticationError):
    """MFA is required but not provided."""
    pass


class InvalidMFACodeError(AuthenticationError):
    """Invalid MFA code."""
    pass


class SessionExpiredError(AuthenticationError):
    """Session has expired."""
    pass


class SessionRevokedError(AuthenticationError):
    """Session has been revoked."""
    pass


def _decode_passlib_ab64(value: str) -> bytes:
    """Decode Passlib's unpadded adapted-base64 representation."""
    normalized = value.replace(".", "+")
    normalized += "=" * (-len(normalized) % 4)
    return base64.b64decode(normalized, validate=True)


def _verify_legacy_pbkdf2_sha256(password: str, encoded_hash: str) -> bool:
    """Verify existing Passlib PBKDF2 hashes without importing deprecated Passlib code.

    Existing account and session-token hashes remain valid during migration.
    New hashes use Argon2id; successful legacy password logins are upgraded.
    """
    try:
        parts = encoded_hash.split("$")
        if len(parts) != 5 or parts[1] != "pbkdf2-sha256":
            return False
        rounds = int(parts[2])
        if rounds < 1000 or rounds > 1_000_000:
            return False
        salt = _decode_passlib_ab64(parts[3])
        expected = _decode_passlib_ab64(parts[4])
        if not salt or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            rounds,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, binascii.Error):
        return False


def hash_password(password: str) -> str:
    """Hash a password with Argon2id."""
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify Argon2id hashes and safely support legacy PBKDF2-SHA256 hashes."""
    if not isinstance(password_hash, str) or not password_hash:
        return False
    if password_hash.startswith("$pbkdf2-sha256$"):
        return _verify_legacy_pbkdf2_sha256(password, password_hash)
    if password_hash.startswith("$argon2"):
        try:
            return password_hasher.verify(password_hash, password)
        except Exception:
            return False
    return False


def hash_token(token: str) -> str:
    """Hash a session or refresh token with Argon2id."""
    return password_hasher.hash(token)


def verify_token(token: str, token_hash: str) -> bool:
    """Verify new Argon2id or existing legacy PBKDF2 token hashes."""
    return verify_password(token, token_hash)


def generate_token(length: int = 32) -> str:
    """Generate cryptographically secure random token."""
    return secrets.token_urlsafe(length)


def generate_session_token() -> str:
    """Generate session token."""
    return f"ses_{generate_token(48)}"


def generate_refresh_token() -> str:
    """Generate refresh token."""
    return f"ref_{generate_token(48)}"


def generate_anonymous_session_id() -> str:
    """Generate anonymous session ID."""
    return f"anon_{generate_token(32)}"


def generate_device_fingerprint(user_agent: str, ip: str, additional: str = "") -> str:
    """Generate device fingerprint from client info."""
    data = f"{user_agent}|{ip}|{additional}"
    return hashlib.sha256(data.encode()).hexdigest()[:32]


def create_access_token(account_id: str, session_id: str, session_type: SessionType, 
                        authorized_person_ids: List[str] = None,
                        caregiver_roles: Dict[str, str] = None,
                        professional_roles: Dict[str, str] = None,
                        mfa_verified: bool = False,
                        session_token: str = None,
                        expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token.

    ``session_token`` is included as a hash so the server can detect
    token rotation (e.g. after logout) by comparing against the DB.
    """
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    token_hash = ""
    if session_token:
        token_hash = hashlib.sha256(session_token.encode()).hexdigest()[:32]
    payload = {
        "sub": account_id,
        "sid": session_id,
        "stype": session_type.value,
        "persons": authorized_person_ids or [],
        "cg_roles": caregiver_roles or {},
        "prof_roles": professional_roles or {},
        "mfa": mfa_verified,
        "sess_hash": token_hash,
        "iat": datetime.now(timezone.utc),
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def create_refresh_token(account_id: str, session_id: str, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT refresh token."""
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    payload = {
        "sub": account_id,
        "sid": session_id,
        "iat": datetime.now(timezone.utc),
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate JWT token."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise SessionExpiredError("Token has expired")
    except jwt.InvalidTokenError as e:
        raise AuthenticationError(f"Invalid token: {str(e)}")


def get_account_by_email(db: Session, email: str) -> Optional[Account]:
    """Get active account by email."""
    return db.query(Account).filter(
        Account.email == email.lower(),
        Account.is_active == True,
        Account.deleted_at == None
    ).first()


def get_account_by_id(db: Session, account_id: str) -> Optional[Account]:
    """Get active account by ID."""
    return db.query(Account).filter(
        Account.id == account_id,
        Account.is_active == True,
        Account.deleted_at == None
    ).first()


def check_account_locked(account: Account) -> bool:
    """Check if account is locked due to failed attempts."""
    if account.locked_until and account.locked_until > datetime.now(timezone.utc):
        return True
    return False


def record_failed_login(db: Session, account: Account) -> None:
    """Record failed login attempt and lock if threshold exceeded."""
    account.failed_login_attempts += 1
    if account.failed_login_attempts >= 5:
        account.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
        
        # Send security alert for account lockout
        send_security_alert(
            event_type="account_locked",
            description=f"Account locked due to excessive failed login attempts: {email}",
            severity="error",
            source_ip=ip_address,
            details={
                "email": email,
                "ip_address": ip_address,
                "locked_until": account.locked_until.isoformat(),
                "failed_attempts": account.failed_login_attempts
            }
        )
    
    db.commit()


def reset_failed_logins(db: Session, account: Account) -> None:
    """Reset failed login counter on successful login."""
    account.failed_login_attempts = 0
    account.locked_until = None
    account.last_login_at = datetime.now(timezone.utc)
    db.commit()


def create_account(
    db: Session,
    email: str,
    password: str,
    account_type: AccountType = AccountType.CAREGIVER,
    display_name: Optional[str] = None,
    auth_provider: AuthProvider = AuthProvider.EMAIL_PASSWORD,
    oauth_provider_id: Optional[str] = None,
    oauth_provider_email: Optional[str] = None,
) -> Account:
    """Create new account."""
    # Check if email exists
    existing = get_account_by_email(db, email)
    if existing:
        raise AuthenticationError("Email already registered")

    account = Account(
        account_type=account_type,
        email=email.lower(),
        password_hash=hash_password(password) if password else None,
        auth_provider=auth_provider,
        oauth_provider_id=oauth_provider_id,
        oauth_provider_email=oauth_provider_email,
        display_name=display_name or email.split("@")[0],
        is_verified=(auth_provider != AuthProvider.EMAIL_PASSWORD),
        email_verified_at=datetime.now(timezone.utc) if auth_provider != AuthProvider.EMAIL_PASSWORD else None,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    
    # Send security alert for new account creation
    send_security_alert(
        event_type="account_created",
        description=f"New account created: {email}",
        severity="info",
        details={
            "email": email,
            "account_id": account.id,
            "account_type": account.account_type.value if account.account_type else None,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
    )
    
    return account


def authenticate_account(
    db: Session,
    email: str,
    password: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    device_fingerprint: Optional[str] = None,
) -> Tuple[Account, Session, str, str]:
    """Authenticate account and create session.
    
    Returns: (account, session, access_token, refresh_token)
    """
    account = get_account_by_email(db, email)
    if not account:
        # Don't reveal if account exists
        raise InvalidCredentialsError("Invalid credentials")

    if check_account_locked(account):
        raise AccountLockedError(account.locked_until)

    if not verify_password(password, account.password_hash or ""):
        record_failed_login(db, account)
        
        # Send security alert for failed login attempt
        send_security_alert(
            event_type="failed_login_attempt",
            description=f"Failed login attempt for account: {email}",
            severity="warning",
            source_ip=ip_address,
            details={
                "email": email,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "device_fingerprint": device_fingerprint,
                "failed_attempts": account.failed_login_attempts
            }
        )
        
        raise InvalidCredentialsError("Invalid credentials")

    if account.auth_provider != AuthProvider.EMAIL_PASSWORD:
        raise AuthenticationError("This account uses a different sign-in method")

    # Upgrade a legacy PBKDF2 password hash after a successful password login.
    # The session commit below persists this change alongside the new session.
    if not (account.password_hash or "").startswith("$argon2id$"):
        account.password_hash = hash_password(password)
        db.add(account)

    # Reset failed attempts
    reset_failed_logins(db, account)
    
    # Send security alert for successful login (could indicate account compromise if from new location/device)
    # In a production system, you might want to implement more sophisticated anomaly detection here
    send_security_alert(
        event_type="successful_login",
        description=f"Successful login for account: {email}",
        severity="info",
        source_ip=ip_address,
        details={
            "email": email,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "device_fingerprint": device_fingerprint,
            "login_time": datetime.now(timezone.utc).isoformat()
        }
    )

    # Get or create device
    device = None
    if device_fingerprint:
        device = db.query(Device).filter(Device.device_fingerprint == device_fingerprint).first()
    
    if not device and device_fingerprint:
        device = Device(
            account_id=account.id,
            device_fingerprint=device_fingerprint,
            platform=None,
            browser=None,
            trust_level=DeviceTrustLevel.UNKNOWN,
        )
        db.add(device)
        db.flush()

    # Determine session type and authorization context
    session_type = SessionType.AUTHENTICATED
    authorized_persons = []
    caregiver_roles = {}
    professional_roles = {}

    # Load authorization context from links
    from app.models.identity import AccountPersonLink, CaregiverRoleLink, ProfessionalRoleLink
    
    person_links = db.query(AccountPersonLink).filter(
        AccountPersonLink.account_id == account.id,
        AccountPersonLink.is_active == True,
        AccountPersonLink.effective_from <= datetime.now(timezone.utc),
        (AccountPersonLink.effective_until == None) | (AccountPersonLink.effective_until > datetime.now(timezone.utc))
    ).all()
    
    for link in person_links:
        authorized_persons.append(link.person_id)
        if link.role:
            caregiver_roles[link.person_id] = link.role.value

    # Check for professional profile
    prof_profile = db.query(ProfessionalProfile).filter(
        ProfessionalProfile.account_id == account.id
    ).first()
    
    if prof_profile and prof_profile.verification_status.value == "verified":
        prof_links = db.query(ProfessionalRoleLink).filter(
            ProfessionalRoleLink.professional_profile_id == prof_profile.id,
            ProfessionalRoleLink.is_active == True,
        ).all()
        for link in prof_links:
            professional_roles[link.person_id] = link.role

    # Check for staff profile
    staff_profile = db.query(StaffProfile).filter(
        StaffProfile.account_id == account.id,
        StaffProfile.is_active == True
    ).first()

    # Create session with hashed token storage
    plain_session_token = generate_session_token()
    session_token_hash_val = hash_token(plain_session_token)
    plain_refresh_token = generate_refresh_token()
    refresh_token_hash_val = hash_token(plain_refresh_token)
    session = Session(
        account_id=account.id,
        session_type=session_type,
        session_token=plain_session_token,
        session_token_hash=session_token_hash_val,
        refresh_token=plain_refresh_token,
        refresh_token_hash=refresh_token_hash_val,
        device_id=device.id if device else None,
        authorized_person_ids=authorized_persons,
        authorized_caregiver_roles=caregiver_roles,
        authorized_professional_roles=professional_roles,
        ip_address=ip_address,
        user_agent=user_agent,
        trust_level=device.trust_level if device else DeviceTrustLevel.UNKNOWN,
        mfa_verified=account.mfa_enabled,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # Generate tokens
    access_token = create_access_token(
        account_id=account.id,
        session_id=session.id,
        session_type=session_type,
        authorized_person_ids=authorized_persons,
        caregiver_roles=caregiver_roles,
        professional_roles=professional_roles,
        mfa_verified=account.mfa_enabled,
        session_token=session.session_token,
    )
    refresh_token = create_refresh_token(account.id, session.id)

    return account, session, access_token, refresh_token


def create_anonymous_session(
    db: Session,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    device_fingerprint: Optional[str] = None,
) -> Tuple[Session, str]:
    """Create anonymous session for first-time users.
    
    Returns: (session, access_token)
    """
    # Get or create device
    device = None
    if device_fingerprint:
        device = db.query(Device).filter(Device.device_fingerprint == device_fingerprint).first()
    
    if not device and device_fingerprint:
        device = Device(
            account_id=None,
            device_fingerprint=device_fingerprint,
            platform=None,
            browser=None,
            trust_level=DeviceTrustLevel.UNKNOWN,
        )
        db.add(device)
        db.flush()

    # Create anonymous session
    plain_session_token = generate_session_token()
    session_token_hash_val = hash_token(plain_session_token)
    session = Session(
        account_id=None,
        session_type=SessionType.ANONYMOUS,
        session_token=plain_session_token,
        session_token_hash=session_token_hash_val,
        anonymous_session_id=anonymous_session_id,
        device_id=device.id if device else None,
        ip_address=ip_address,
        user_agent=user_agent,
        trust_level=DeviceTrustLevel.UNKNOWN,
        expires_at=datetime.now(timezone.utc) + timedelta(days=ANONYMOUS_SESSION_EXPIRE_DAYS),
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # Create anonymous session record tracker
    anon_record = AnonymousSessionRecord(
        anonymous_session_id=anonymous_session_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=ANONYMOUS_SESSION_EXPIRE_DAYS),
    )
    db.add(anon_record)
    db.commit()

    # Generate access token (no refresh for anonymous)
    access_token = create_access_token(
        account_id="",
        session_id=session.id,
        session_type=SessionType.ANONYMOUS,
        authorized_person_ids=[],
        session_token=session.session_token,
        expires_delta=timedelta(days=ANONYMOUS_SESSION_EXPIRE_DAYS),
    )

    return session, access_token


def refresh_access_token(db: Session, refresh_token: str) -> Tuple[str, str]:
    """Refresh access token using refresh token.
    
    Returns: (new_access_token, new_refresh_token)
    """
    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise AuthenticationError("Invalid token type")

    session_id = payload.get("sid")
    account_id = payload.get("sub")
    
    session = db.query(Session).filter(
        Session.id == session_id,
        Session.refresh_token == refresh_token,
        Session.revoked_at == None,
        Session.expires_at > datetime.now(timezone.utc),
    ).first()
    
    if not session:
        raise SessionRevokedError("Session not found or revoked")

    account = get_account_by_id(db, account_id)
    if not account:
        raise AuthenticationError("Account not found")

    # Rotate refresh token
    new_refresh_token = generate_refresh_token()
    new_refresh_token_hash = hash_token(new_refresh_token)
    session.refresh_token = new_refresh_token
    session.refresh_token_hash = new_refresh_token_hash
    session.expires_at = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    db.commit()

    # Reload authorization context
    from app.models.identity import AccountPersonLink, CaregiverRoleLink, ProfessionalRoleLink, ProfessionalProfile
    
    person_links = db.query(AccountPersonLink).filter(
        AccountPersonLink.account_id == account.id,
        AccountPersonLink.is_active == True,
    ).all()
    
    authorized_persons = [link.person_id for link in person_links]
    caregiver_roles = {link.person_id: link.role.value for link in person_links if link.role}
    
    prof_profile = db.query(ProfessionalProfile).filter(
        ProfessionalProfile.account_id == account.id
    ).first()
    
    professional_roles = {}
    if prof_profile and prof_profile.verification_status.value == "verified":
        prof_links = db.query(ProfessionalRoleLink).filter(
            ProfessionalRoleLink.professional_profile_id == prof_profile.id,
            ProfessionalRoleLink.is_active == True,
        ).all()
        for link in prof_links:
            professional_roles[link.person_id] = link.role

    new_access_token = create_access_token(
        account_id=account.id,
        session_id=session.id,
        session_type=session.session_type,
        authorized_person_ids=authorized_persons,
        caregiver_roles=caregiver_roles,
        professional_roles=professional_roles,
        mfa_verified=account.mfa_enabled,
    )

    return new_access_token, new_refresh_token


def validate_session(db: Session, access_token: str) -> Session:
    """Validate access token and return session.

    The access token is a JWT whose ``sid`` claim is the session ID.
    We look up the session by that ID and verify it has not been revoked
    or expired in the database. We also compare the session_token hash
    embedded in the JWT against the current DB value to detect rotation.
    """
    payload = decode_token(access_token)
    if payload.get("type") != "access":
        raise AuthenticationError("Invalid token type")

    session_id = payload.get("sid")
    session = db.query(Session).filter(
        Session.id == session_id,
        Session.revoked_at == None,
        Session.expires_at > datetime.now(timezone.utc),
    ).first()

    if not session:
        raise SessionRevokedError("Session not found or revoked")

    # Detect token rotation: the JWT embeds a hash of the session_token.
    # If the DB session_token has changed (e.g. after logout+login),
    # the hashes won't match and we reject the old token.
    jwt_hash = payload.get("sess_hash", "")
    if jwt_hash:
        db_hash = hashlib.sha256(session.session_token.encode()).hexdigest()[:32]
        if jwt_hash != db_hash:
            raise SessionRevokedError("Session token has been rotated")

    # Update last activity
    session.last_activity_at = datetime.now(timezone.utc)
    db.commit()

    return session


def revoke_session(db: Session, session_id: str, reason: str = "user_logout") -> bool:
    """Revoke a session."""
    session = db.query(Session).filter(Session.id == session_id).first()
    if session and not session.revoked_at:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoked_reason = reason
        db.commit()
        return True
    return False


def revoke_all_sessions(db: Session, account_id: str, except_session_id: Optional[str] = None, reason: str = "security") -> int:
    """Revoke all sessions for an account."""
    query = db.query(Session).filter(
        Session.account_id == account_id,
        Session.revoked_at == None,
    )
    if except_session_id:
        query = query.filter(Session.id != except_session_id)
    
    sessions = query.all()
    for session in sessions:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoked_reason = reason
    db.commit()
    return len(sessions)


def create_claim_session(
    db: Session,
    anonymous_session_id: str,
    account: Account,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Tuple[Session, str, str]:
    """Create session for claiming anonymous record.
    
    Returns: (session, access_token, refresh_token)
    """
    # Verify anonymous session exists and is unclaimed
    anon_record = db.query(AnonymousSessionRecord).filter(
        AnonymousSessionRecord.anonymous_session_id == anonymous_session_id,
        AnonymousSessionRecord.claimed == False,
        AnonymousSessionRecord.expires_at > datetime.now(timezone.utc),
    ).first()
    
    if not anon_record:
        raise AuthenticationError("Anonymous session not found or already claimed")

    # Create claiming session
    session = Session(
        account_id=account.id,
        session_type=SessionType.CLAIMING,
        session_token=generate_session_token(),
        refresh_token=generate_refresh_token(),
        anonymous_session_id=anonymous_session_id,
        ip_address=ip_address,
        user_agent=user_agent,
        trust_level=DeviceTrustLevel.UNKNOWN,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),  # Short-lived for claim flow
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    access_token = create_access_token(
        account_id=account.id,
        session_id=session.id,
        session_type=SessionType.CLAIMING,
        authorized_person_ids=[],
        session_token=session.session_token,
        expires_delta=timedelta(hours=1),
    )
    refresh_token = create_refresh_token(account.id, session.id)

    return session, access_token, refresh_token


def complete_claim(
    db: Session,
    anonymous_session_id: str,
    account: Account,
    claim_session: Session,
) -> List[str]:
    """Complete anonymous record claim, transferring ownership.
    
    Returns: List of claimed person IDs
    """
    # Get anonymous records
    anon_records = db.query(AnonymousSessionRecord).filter(
        AnonymousSessionRecord.anonymous_session_id == anonymous_session_id,
        AnonymousSessionRecord.claimed == False,
    ).all()
    
    claimed_person_ids = []
    
    for anon_record in anon_records:
        # Transfer person ownership
        if anon_record.person_id:
            # Create account-person link
            from app.models.identity import AccountPersonLink, CaregiverRoleLink
            
            link = AccountPersonLink(
                account_id=account.id,
                person_id=anon_record.person_id,
                link_type="caregiver_for",
                role="primary",
                verified_at=datetime.now(timezone.utc),
                is_active=True,
            )
            db.add(link)
            
            caregiver_link = CaregiverRoleLink(
                account_id=account.id,
                person_id=anon_record.person_id,
                role="primary",
                is_active=True,
            )
            db.add(caregiver_link)
            
            claimed_person_ids.append(anon_record.person_id)
        
        # Mark as claimed
        anon_record.claimed = True
        anon_record.claimed_by_account_id = account.id
        anon_record.claimed_at = datetime.now(timezone.utc)
        anon_record.claim_session_id = claim_session.id
    
    # Revoke claiming session
    claim_session.revoked_at = datetime.now(timezone.utc)
    claim_session.revoked_reason = "claim_completed"
    
    db.commit()
    
    return claimed_person_ids