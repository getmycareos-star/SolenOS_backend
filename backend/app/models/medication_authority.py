from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
import uuid
from app.core.database import Base


class MedicationAuthorityVerification(Base):
    __tablename__ = "medication_authority_verifications"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    evidence_id = Column(String, ForeignKey("evidence.id"), nullable=False, unique=True, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    source_text_sha256 = Column(String(64), nullable=False)
    authority_role = Column(String, nullable=False)
    verifier_identity = Column(String, nullable=False)
    verification_method = Column(String, nullable=False)
    verified_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    verified_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
