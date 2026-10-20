"""Caregiver intelligence models for SolenOS.

    person = relationship("Person", foreign_keys=[person_id])
class CaregiverObservation(Base):
    """Represents an observation made by a caregiver about a person's condition or behavior."""
    __tablename__ = "caregiver_observations"

    id = Column(String, primary_key=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # Observation details
    description = Column(Text, nullable=False)
    observation_type = Column(String, nullable=False)

    # Timing
    event_time = Column(DateTime(timezone=True), nullable=False)
    event_time_precision = Column(String, nullable=True)

    # Additional context
    context = Column(Text, nullable=True)
    location = Column(String, nullable=True)
    duration_context = Column(String, nullable=True)
    evidence_ref = Column(String, nullable=True)
    information_visibility = Column(SQLEnum(InformationVisibility), nullable=False, default=InformationVisibility.PRIVATE)
    is_promoted_to_shared = Column(Boolean, nullable=False, default=False)
    promoted_at = Column(DateTime(timezone=True), nullable=True)
    promoted_by_caregiver_id = Column(String, nullable=True)
    extra_metadata = Column(JSON, nullable=True)
    report_time = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    caregiver = relationship("Account", foreign_keys=[caregiver_id])

    __table_args__ = (
        Index("ix_caregiver_observation_person_time", "person_id", "event_time"),
        Index("ix_caregiver_observation_person_type", "person_id", "observation_type"),
    )
class CaregiverResponsibility(Base):
    """Represents a responsibility or role that a caregiver has for a person."""

    __tablename__ = "caregiver_responsibilities"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)  # Link to the caregiver's account

    # Responsibility details
    responsibility_type = Column(String, nullable=False)  # e.g., "medication_management", "transportation", "personal_care"
    scope = Column(String, nullable=False)  # e.g., "full", "partial", "supervisory", "emergency_only"
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
    is_joint = Column(String, nullable=False, default="false")  # Whether this responsibility is shared with other caregivers
    notes = Column(Text, nullable=True)

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    caregiver = relationship("Account", foreign_keys=[caregiver_id])

    __table_args__ = (
        Index("ix_caregiver_responsibility_person_status", "person_id", "status"),
        Index("ix_caregiver_responsibility_person_type", "person_id", "responsibility_type"),
        Index("ix_caregiver_responsibility_person_effective", "person_id", "effective_start", "effective_end"),
    )


class CaregiverProfile(Base):
    """Caregiver profile representing a caregiver's identity and qualifications."""
    __tablename__ = "caregiver_profiles"

    id = Column(String, primary_key=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    # Additional fields can be added as needed
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])


class CaregiverRole(Base):
    """Represents a role that a caregiver can have."""
    __tablename__ = "caregiver_roles"

    id = Column(String, primary_key=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # Role details
    role_type = Column(String, nullable=False)  # e.g., "primary", "secondary", "respite", "specialist"
    description = Column(Text, nullable=True)

    # Timing
    effective_start = Column(DateTime(timezone=True), nullable=False)
    effective_end = Column(DateTime(timezone=True), nullable=True)  # Null if ongoing
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Status
    is_active = Column(String, nullable=False, default="true")  # true/false

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    caregiver = relationship("Account", foreign_keys=[caregiver_id])


class CaregiverObservation(Base):
    """Represents an observation made by a caregiver about a person's condition or behavior."""
    __tablename__ = "caregiver_observations"

    id = Column(String, primary_key=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)

    # Observation details
    observation_type = Column(String, nullable=False)  # e.g., "behavior", "mood", "physical_condition", "cognitive"
    description = Column(Text, nullable=False)
    severity = Column(String, nullable=True)  # e.g., "mild", "moderate", "severe"

    # Timing
    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    recorded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Additional context
    context = Column(Text, nullable=True)  # Additional context about the observation
    location = Column(String, nullable=True)  # Where the observation was made

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    caregiver = relationship("Account", foreign_keys=[caregiver_id])

    __table_args__ = (
        Index("ix_caregiver_observation_person_time", "person_id", "observed_at"),
        Index("ix_caregiver_observation_person_type", "person_id", "observation_type"),
    )


class ResponsibilityGap(Base):
    """Represents a gap in caregiver responsibilities that needs to be addressed."""
    __tablename__ = "responsibility_gaps"

    id = Column(String, primary_key=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    # The responsibility type that is lacking
    responsibility_type = Column(String, nullable=False)
    # Description of the gap
    description = Column(Text, nullable=False)
    # Severity of the gap
    severity = Column(String, nullable=False)  # e.g., "low", "medium", "high", "critical"
    # Status of the gap
    status = Column(String, nullable=False, default="open")  # open, addressing, resolved
    # Timing
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])

