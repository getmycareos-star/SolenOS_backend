import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.sql import func

from app.core.database import Base


class InteractionSession(Base):
    """Interaction lifecycle state, separate from bearer-authentication sessions and care reality."""

    __tablename__ = "interaction_sessions"
    __table_args__ = (
        UniqueConstraint(
            "interaction_session_id",
            "person_id",
            "caregiver_id",
            name="uq_interaction_session_scope",
        ),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    interaction_session_id = Column(String(128), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    paused_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
