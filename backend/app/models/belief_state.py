"""Belief State model for hierarchical Bayesian belief updating."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, JSON
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.identity import Person


class BeliefState(Base):
    """Represents a posterior distribution over latent care states for a person."""

    __tablename__ = "belief_states"

    id = Column(String, primary_key=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # Latent state variables (e.g., symptom severity, medication efficacy)
    # Stored as a JSON dict mapping variable name to distribution parameters.
    # For simplicity, we store mean and variance for each variable.
    state_variables = Column(JSON, nullable=False)  # e.g., {"symptom_severity": {"mean": 0.5, "variance": 0.1}}

    # Timestamp of the belief update
    updated_at = Column(DateTime(timezone=True), nullable=False)

    # Relationship to person
    person = relationship("Person", foreign_keys=[person_id])

    __table_args__ = (
        Index("ix_belief_state_person_updated", "person_id", "updated_at"),
    )
