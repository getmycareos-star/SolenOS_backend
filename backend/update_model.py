import os
content = '''\"\"\"Caregiver intelligence models for SolenOS.

Represents caregiver responsibilities, capabilities, and relationships.\"\"\"

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Enum as SQLEnum, JSON, func, Index
from sqlalchemy.orm import relationship
import uuid
from enum import Enum
from app.core.database import Base
from app.models.identity import Person
from app.core.caregiver_enums import ResponsibilityStatus, AcceptanceStatus


class CaregiverResponsibility(Base):
    \"\"\"Represents a responsibility or role that a caregiver has for a person.\"\"\"

    __tablename__ = \"caregiver_responsibilities\"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey(\"persons.id\"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey(\"accounts.id\"), nullable=False, index=True)  # Link to the caregiver's account

    # Responsibility details
    responsibility_type = Column(String, nullable=False)  # e.g., \"medication_management\", \"transportation\", \"personal_care\"
    scope = Column(String, nullable=False)  # e.g., \"full\", \"partial\", \"supervisory\", \"emergency_only\"
    description = Column(Text, nullable=True)

    # Timing
    effective_start = Column(DateTime(timezone=True), nullable=False)
    effective_end = Column(DateTime(timezone=True), nullable=True)  # Null if ongoing
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Status
    status = Column(SQLEnum(ResponsibilityStatus), nullable=False, default=ResponsibilityStatus.ACTIVE)
    acceptance_status = Column(SQLEnum(AcceptanceStatus), nullable=True)  # May be null if not yet offered/accepted

    # Relationship details
    is_joint = Column(String, nullable=False, default=\"false\")  # Whether this responsibility is shared with other caregivers
    notes = Column(Text, nullable=True)

    # Relationships
    person = relationship(\"Person\", foreign_keys=[person_id])
    caregiver = relationship(\"Account\", foreign_keys=[caregiver_id])

    __table_args__ = (
        Index(\"ix_caregiver_responsibility_person_status\", \"person_id\", \"status\"),
        Index(\"ix_caregiver_responsibility_person_type\", \"person_id\", \"responsibility_type\"),
        Index(\"ix_caregiver_responsibility_person_effective\", \"person_id\", \"effective_start\", \"effective_end\"),
    )


class CaregiverProfile(Base):
    \"\"\"Caregiver profile representing a caregiver's identity and qualifications.\"\"\"
    __tablename__ = \"caregiver_profiles\"

    id = Column(String, primary_key=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey(\"persons.id\"), nullable=False, index=True)
    # Additional fields can be added as needed
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    person = relationship(\"Person\", foreign_keys=[person_id])


class CaregiverRole(Base):
    \"\"\"Represents a role that a caregiver can have.\"\"\"
    __tablename__ = \"caregiver_roles\"

    id = Column(String, primary_key=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey(\"accounts.id\"), nullable=False, index=True)
    person_id = Column(String, ForeignKey(\"persons.id\"), nullable=False, index=True)

    # Role details
    role_type = Column(String, nullable=False)  # e.g., \"primary\", \"secondary\", \"respite\", \"specialist\"
    description = Column(Text, nullable=True)

    # Timing
    effective_start = Column(DateTime(timezone=True), nullable=False)
    effective_end = Column(DateTime(timezone=True), nullable=True)  # Null if ongoing
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Status
    is_active = Column(String, nullable=False, default=\"true\")  # true/false

    # Relationships
    person = relationship(\"Person\", foreign_keys=[person_id])
    caregiver = relationship(\"Account\", foreign_keys=[caregiver_id])


class ResponsibilityGap(Base):
    \"\"\"Represents a gap in caregiver responsibilities that needs to be addressed.\"\"\"
    __tablename__ = \"responsibility_gaps\"

    id = Column(String, primary_key=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey(\"persons.id\"), nullable=False, index=True)
    # The responsibility type that is lacking
    responsibility_type = Column(String, nullable=False)
    # Description of the gap
    description = Column(Text, nullable=False)
    # Severity of the gap
    severity = Column(String, nullable=False)  # e.g., \"low\", \"medium\", \"high\", \"critical\"
    # Status of the gap
    status = Column(String, nullable=False, default=\"open\")  # open, addressing, resolved
    # Timing
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    person = relationship(\"Person\", foreign_keys=[person_id])
"""

with open('C:\\solenos\\backend\\app\\models\\caregiver_intelligence.py', 'w') as f:
    f.write(content)

print('File rewritten successfully')
