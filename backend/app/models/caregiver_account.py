import uuid
from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class CaregiverAccount(Base):
    """Email/password account linked to one existing care record.

    Passwords are stored as salted PBKDF2 hashes, never as plaintext.
    The account provides a way to regain a scoped bearer session; it does not
    grant professional privileges or access to other care records.
    """
    __tablename__ = "caregiver_accounts"
    __table_args__ = (
        UniqueConstraint("email_normalized", name="uq_caregiver_accounts_email_normalized"),
        UniqueConstraint("person_id", name="uq_caregiver_accounts_person_id"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email_normalized = Column(String(320), nullable=False)
    password_hash = Column(String(256), nullable=False)
    person_id = Column(String, ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
