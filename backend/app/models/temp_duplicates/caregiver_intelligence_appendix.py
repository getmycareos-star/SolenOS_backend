
class CaregiverPerspective(Base):
    """Represents a caregiver's perspective or claim about a person's condition or needs."""
    __tablename__ = "caregiver_perspectives"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    claim_type = Column(SQLEnum(ClaimType), nullable=False)
    claim_content = Column(Text, nullable=False)
    context = Column(Text, nullable=True)
    evidence_ref = Column(String, nullable=True)
    information_visibility = Column(SQLEnum(InformationVisibility), nullable=False, default=InformationVisibility.PRIVATE)
    is_promoted_to_shared = Column(Boolean, nullable=False, default=False)
    promoted_at = Column(DateTime(timezone=True), nullable=True)
    promoted_by_caregiver_id = Column(String, nullable=True)
    extra_metadata = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    caregiver = relationship("Account", foreign_keys=[caregiver_id])


class CaregiverHandoff(Base):
    """Represents a handoff of responsibility between caregivers."""
    __tablename__ = "caregiver_handoffs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    from_caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    to_caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    responsibility_type = Column(String, nullable=False)
    scope = Column(String, nullable=True)
    completion_evidence = Column(String, nullable=True)
    is_completed = Column(Boolean, nullable=False, default=False)
    provenance = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    from_caregiver = relationship("Account", foreign_keys=[from_caregiver_id])
    to_caregiver = relationship("Account", foreign_keys=[to_caregiver_id])


class CaregiverActivity(Base):
    """Represents an activity performed by a caregiver."""
    __tablename__ = "caregiver_activities"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    activity_type = Column(String, nullable=False)  # Simplified; could link to enum table
    description = Column(Text, nullable=False)
    related_entity_type = Column(String, nullable=True)
    related_entity_id = Column(String, nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    is_anonymous = Column(Boolean, nullable=False, default=False)
    shared_account_id = Column(String, nullable=True)
    extra_metadata = Column(JSON, nullable=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    caregiver = relationship("Account", foreign_keys=[caregiver_id])


class CaregiverCommunication(Base):
    """Represents a communication between caregivers."""
    __tablename__ = "caregiver_communications"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    from_caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    to_caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=True, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    direction = Column(String, nullable=False)  # Simplified; could link to enum table
    subject = Column(String, nullable=True)
    content_summary = Column(Text, nullable=True)
    is_private = Column(Boolean, nullable=False, default=True)
    visibility = Column(String, nullable=False, default=InformationVisibility.RESTRICTED.value)  # Simplified
    acknowledgment_level = Column(String, nullable=True)  # Simplified
    acknowledgment_time = Column(DateTime(timezone=True), nullable=True)
    extra_metadata = Column(JSON, nullable=True)
    communication_time = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    from_caregiver = relationship("Account", foreign_keys=[from_caregiver_id])
    to_caregiver = relationship("Account", foreign_keys=[to_caregiver_id])


class CareNetworkRelationship(Base):
    """Represents a relationship in the care network."""
    __tablename__ = "care_network_relationships"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    from_caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    to_caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=True, index=True)
    relationship_type = Column(String, nullable=False)  # Simplified
    description = Column(Text, nullable=True)
    effective_start = Column(DateTime(timezone=True), nullable=False)
    effective_end = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    provenance = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    from_caregiver = relationship("Account", foreign_keys=[from_caregiver_id])
    to_caregiver = relationship("Account", foreign_keys=[to_caregiver_id])


class PerspectiveConflict(Base):
    """Represents a conflict between caregivers' perspectives."""
    __tablename__ = "perspective_conflicts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_a_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    caregiver_b_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    conflict_type = Column(String, nullable=False)
    claim_a = Column(Text, nullable=False)
    claim_b = Column(Text, nullable=False)
    context_a = Column(Text, nullable=True)
    context_b = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="unresolved")
    resolution = Column(Text, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    evidence_ref = Column(String, nullable=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    caregiver_a = relationship("Account", foreign_keys=[caregiver_a_id])
    caregiver_b = relationship("Account", foreign_keys=[caregiver_b_id])


class DuplicateAction(Base):
    """Represents a potential duplicate action between caregivers."""
    __tablename__ = "duplicate_actions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    caregiver_a_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    caregiver_b_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    action_type = Column(String, nullable=False)
    action_description_a = Column(Text, nullable=False)
    action_description_b = Column(Text, nullable=False)
    time_a = Column(DateTime(timezone=True), nullable=True)
    time_b = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=False, default="potential_duplicate")
    resolution = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    caregiver_a = relationship("Account", foreign_keys=[caregiver_a_id])
    caregiver_b = relationship("Account", foreign_keys=[caregiver_b_id])


class CaregiverContext(Base):
    """Represents contextual information about a caregiver's availability."""
    __tablename__ = "caregiver_contexts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    caregiver_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    proximity_category = Column(String, nullable=True)  # Simplified
    observation_window_start = Column(String, nullable=True)
    observation_window_end = Column(String, nullable=True)
    availability_notes = Column(Text, nullable=True)
    context_metadata = Column(JSON, nullable=True)
    effective_start = Column(DateTime(timezone=True), nullable=False)
    effective_end = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    caregiver = relationship("Account", foreign_keys=[caregiver_id])
