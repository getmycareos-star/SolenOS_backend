from typing import Optional, Dict, Any
import time
import json
import requests
from jose import jwt
from jose.exceptions import JOSEError
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.core.database import get_db
from app.core.config import get_settings
from app.models.identity import Account, Session, AccountType, AuthProvider
from sqlalchemy import func

settings = get_settings()

# Cache for the Keycloak public key
_keycloak_public_key = None
_keycloak_public_key_expires_at = 0

def get_keycloak_public_key() -> str:
    '''Fetch the public key from the Keycloak realm and cache it.'''
    global _keycloak_public_key, _keycloak_public_key_expires_at
    current_time = time.time()
    if _keycloak_public_key is not None and current_time < _keycloak_public_key_expires_at:
        return _keycloak_public_key

    # Fetch the OpenID Connect configuration to get the public key
    keycloak_url = settings.keycloak_url.rstrip('/')
    realm = settings.keycloak_realm
    config_url = f"{keycloak_url}/realms/{realm}/.well-known/openid-configuration"
    try:
        response = requests.get(config_url, timeout=10)
        response.raise_for_status()
        config = response.json()
        jwks_uri = config["jwks_uri"]
        jwks_response = requests.get(jwks_uri, timeout=10)
        jwks_response.raise_for_status()
        jwks = jwks_response.json()
        # We assume there is only one key in the JWKS for simplicity
        # In production, we should check the key id (kid) in the token header
        public_key = jwks["keys"][0]
        # Convert the JWK to a PEM string
        from jose.utils import RSAAlgorithm
        public_key_pem = RSAAlgorithm.to_jwk(public_key)
        # Cache the public key for 5 minutes (300 seconds)
        _keycloak_public_key = public_key_pem
        _keycloak_public_key_expires_at = current_time + 300
        return public_key_pem
    except Exception as e:
        # Log the error and raise an exception
        raise Exception(f"Failed to fetch Keycloak public key: {e}")

def validate_keycloak_token(token: str) -> Dict[str, Any]:
    '''Validate a Keycloak JWT token and return the payload.'''
    try:
        public_key = get_keycloak_public_key()
        # Decode and validate the token
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],  # Keycloak uses RS256 by default
            audience=settings.keycloak_client_id,  # We expect the token to be audience for our client
            issuer=f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"
        )
        return payload
    except JOSEError as e:
        raise Exception(f"Token validation failed: {e}")
    except Exception as e:
        raise Exception(f"Token validation failed: {e}")

def get_or_create_account(db: Session, user_info: Dict[str, Any]) -> Account:
    '''Get or create a local Account for the Keycloak user.'''
    # Use the 'sub' claim as the unique identifier for the user in Keycloak
    keycloak_user_id = user_info.get("sub")
    email = user_info.get("email")

    # Look for an existing account by email (since email is unique in our Account model)
    # We also want to link the Keycloak user ID to the account.
    account = db.query(Account).filter(Account.email == email).first()
    if account:
        # Update the account to link the Keycloak user ID if not already set
        if account.oauth_provider_id != keycloak_user_id:
            account.oauth_provider_id = keycloak_user_id
            account.auth_provider = AuthProvider.KEYCLOAK
            db.commit()
        return account
    else:
        # Create a new account
        account = Account(
            email=email,
            oauth_provider_id=keycloak_user_id,
            auth_provider=AuthProvider.KEYCLOAK,
            is_active=True,
            is_verified=True,  # Assume the email is verified by Keycloak
            email_verified_at=func.now(),
            display_name=user_info.get("name"),
            # We don't have a password because it's OAuth-only
            password_hash=None,
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        return account

def get_or_create_session(db: Session, account: Account) -> Session:
    '''Get or create a session for the account.'''
    # We want to have one active session per account for simplicity.
    # In a real application, we might allow multiple sessions.
    session = db.query(Session).filter(
        and_(
            Session.account_id == account.id,
            Session.revoked_at.is_(None),
            Session.expires_at > func.now()
        )
    ).first()
    if session:
        # Update the last activity time
        session.last_activity_at = func.now()
        db.commit()
        return session
    else:
        # Create a new session
        import uuid
        session_token = str(uuid.uuid4())
        refresh_token = str(uuid.uuid4())
        session = Session(
            account_id=account.id,
            session_token=session_token,
            refresh_token=refresh_token,
            session_type=SessionType.AUTHENTICATED,
            expires_at=func.now() + settings.access_token_expire_minutes * 60,  # Convert minutes to seconds
            authorized_person_ids=[],  # We will update this later based on the account's links
            authorized_caregiver_roles={},
            authorized_professional_roles={},
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

def update_session_authorizations(db: Session, session: Session, account: Account):
    '''Update the session's authorized person IDs and roles based on the account's links.'''
    # Get all active account-person links for this account
    from app.models.identity import AccountPersonLink, CaregiverRoleLink, ProfessionalRoleLink
    links = db.query(AccountPersonLink).filter(
        and_(
            AccountPersonLink.account_id == account.id,
            AccountPersonLink.is_active == True
        )
    ).all()

    authorized_person_ids = []
    authorized_caregiver_roles = {}  # person_id -> role
    authorized_professional_roles = {}  # person_id -> role

    for link in links:
        authorized_person_ids.append(link.person_id)
        # If the link has a caregiver role, add it
        if link.role:
            authorized_caregiver_roles[link.person_id] = link.role
        # Note: we don't have a direct link to professional roles in the AccountPersonLink
        # We would need to query the ProfessionalRoleLink separately if we want to include them.
        # For now, we only include caregiver roles from the account-person links.

    # Update the session
    session.authorized_person_ids = authorized_person_ids
    session.authorized_caregiver_roles = authorized_caregiver_roles
    session.authorized_professional_roles = authorized_professional_roles
    db.commit()

def authenticate_via_keycloak(token: str, db: Session) -> tuple[Account, Session]:
    '''Authenticate a user via a Keycloak token and return the account and session.'''
    # Validate the token
    user_info = validate_keycloak_token(token)
    # Get or create the account
    account = get_or_create_account(db, user_info)
    # Get or create the session
    session = get_or_create_session(db, account)
    # Update the session's authorizations
    update_session_authorizations(db, session, account)
    return account, session
