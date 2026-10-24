from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


class CareCircle(Base):
    __tablename__ = "care_circles"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CareCircleMembership(Base):
    __tablename__ = "care_circle_memberships"
    __table_args__ = (
        UniqueConstraint("care_circle_id", "member_caregiver_id", name="uq_care_circle_member"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    care_circle_id = Column(String, ForeignKey("care_circles.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    member_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=False, index=True)
    role = Column(String, nullable=False, default="family_member")
    role_description = Column(Text, nullable=True)
    access_level = Column(String, nullable=False, default="contributor")
    can_view_information = Column(Boolean, nullable=False, default=True)
    can_contribute_information = Column(Boolean, nullable=False, default=True)
    can_receive_notifications = Column(Boolean, nullable=False, default=True)
    can_participate_in_decisions = Column(Boolean, nullable=False, default=False)
    relationship_to_person = Column(String, nullable=True)
    invitation_status = Column(String, nullable=False, default="active")
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    left_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
