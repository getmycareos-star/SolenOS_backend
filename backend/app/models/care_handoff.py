import uuid

from sqlalchemy import Column, DateTime, ForeignKey, JSON, String, Text, Index
from sqlalchemy.sql import func

from app.core.database import Base


class CareHandoff(Base):
    __tablename__ = "care_handoffs"
    __table_args__ = (
        Index("ix_care_handoffs_person_status_created", "person_id", "status", "created_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    assigned_to_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True, index=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=False)
    outstanding_items = Column(JSON, nullable=False, default=list)
    recent_changes = Column(JSON, nullable=False, default=list)
    safety_concerns = Column(JSON, nullable=False, default=list)
    status = Column(String, nullable=False, default="open", index=True)  # open, acknowledged, closed
    acknowledged_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    closed_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CareHandoffEvent(Base):
    __tablename__ = "care_handoff_events"
    __table_args__ = (
        Index("ix_care_handoff_events_handoff_created", "handoff_id", "created_at"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    handoff_id = Column(String, ForeignKey("care_handoffs.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    actor_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    event_type = Column(String, nullable=False)  # created, acknowledged, closed
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
