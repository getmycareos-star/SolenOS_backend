from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.auth_identity import AuthAccount, AuthSession
from app.services.auth_service import (
    authenticate_account, create_account, create_session, resolve_session, revoke_session,
)


def make_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[AuthAccount.__table__, AuthSession.__table__])
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_account_login_session_and_revocation_lifecycle():
    db = make_db()
    try:
        account = create_account(db, " CAREGIVER@EXAMPLE.COM ", "strong-password-2026", "Caregiver")
        assert account.email == "caregiver@example.com"
        assert authenticate_account(db, "caregiver@example.com", "strong-password-2026").id == account.id
        assert authenticate_account(db, "caregiver@example.com", "wrong-password-2026") is None

        raw_token, session = create_session(db, account)
        assert raw_token
        assert session.token_digest != raw_token
        assert len(session.token_digest) == 64
        assert resolve_session(db, raw_token).id == account.id
        assert revoke_session(db, raw_token) is True
        assert resolve_session(db, raw_token) is None
        assert revoke_session(db, raw_token) is False
    finally:
        db.close()


def test_expired_session_is_rejected():
    db = make_db()
    try:
        account = create_account(db, "expired@example.com", "strong-password-2026")
        raw_token, session = create_session(db, account)
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
        assert resolve_session(db, raw_token) is None
    finally:
        db.close()
