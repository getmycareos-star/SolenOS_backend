class CareEventRecurrenceGroup(Base):
    """Pattern recognition model - represents a meaningful recurrence of observations.
    
    Implements the Pattern Recognition requirements from SolenOS specification:
    - Strict separation between repetition, pattern, and causation
    - Evidence-based pattern detection with user verification
    - No automatic causation inference
    - Comprehensive pattern attributes as specified
    """
    __tablename__ = "care_event_recurrence_groups"

    # Identity
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    version = Column(Integer, nullable=False, default=1)  # For optimistic locking/versioning
    
    # Person this pattern is about
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    # Pattern categorization
    category = Column(String, nullable=False, index=True)  # e.g., SYMPTOM, BEHAVIOR, MEDICATION, RESPONSIBILITY, APPOINTMENT, ROUTINE, CAREGIVER_CONCERN
    subcategory = Column(String, nullable=True)  # More specific categorization within category

    # Related entities (what this pattern is about)
    related_entity_type = Column(String, nullable=True)  # e.g., SYMPTOM, BEHAVIOR, MEDICATION_NAME, RESPONSIBILITY_TYPE
    related_entity_id = Column(String, nullable=True, index=True)  # Reference to specific entity
    related_entity_label = Column(String, nullable=True)  # Human-readable label for the entity

    # Supporting observations (the actual evidence)
    supporting_evidence_ids = Column(JSONList, nullable=False)  # List of Evidence IDs that support this pattern
    supporting_event_ids = Column(JSONList, nullable=False)   # List of CareEvent IDs that are instances of this pattern
    
    # Occurrence tracking
    occurrence_count = Column(Integer, nullable=False, default=0)
    first_occurrence_time = Column(DateTime(timezone=True), nullable=True, index=True)
    last_occurrence_time = Column(DateTime(timezone=True), nullable=True, index=True)
    occurrence_dates = Column(JSONList, nullable=True)  # List of ISO timestamps for each occurrence
    
    # Observation interval and temporal characteristics
    observation_interval_mean = Column(Float, nullable=True)  # Average time between occurrences (days)
    observation_interval_stddev = Column(Float, nullable=True)  # Standard deviation of intervals
    observation_interval_min = Column(Float, nullable=True)  # Minimum interval observed
    observation_interval_max = Column(Float, nullable=True)  # Maximum interval observed
    
    # Recurrence characteristics
    recurrence_characteristics = Column(JSONDict, nullable=True)  # Detailed recurrence analysis
    pattern_type = Column(String, nullable=True)  # frequency, duration, recurrence, contextual, routine, etc.
    trend = Column(String, nullable=True)  # increasing, decreasing, stable, fluctuating
    
    # Contextual information
    context = Column(Text, nullable=True)  # Documented context surrounding the observations
    baseline_comparison = Column(Text, nullable=True)  # Comparison with established baseline if available
    baseline_available = Column(Boolean, nullable=False, default=False)  # Whether a reliable baseline exists
    
    # Evidence threshold and validation
    evidence_threshold_met = Column(Boolean, nullable=False, default=False)  # Whether minimum evidence threshold is satisfied
    evidence_threshold_details = Column(Text, nullable=True)  # Explanation of how threshold was evaluated
    
    # Confidence and uncertainty (separated as per specification)
    pattern_confidence = Column(Float, nullable=True)  # Confidence that observations recur as a pattern
    confidence_breakdown = Column(JSONDict, nullable=True)  # Breakdown of confidence factors
    uncertainty_level = Column(String, nullable=True)  # Level of uncertainty in pattern detection
    uncertainty_details = Column(Text, nullable=True)  # Detailed explanation of uncertainties
    
    # Alternative interpretations (as per specification)
    alternative_interpretations = Column(JSONList, nullable=True)  # List of alternative explanations considered
    
    # Causation status (explicitly not inferred)
    causation_status = Column(String, nullable=False, default="NOT_ESTABLISHED")  # Always NOT_ESTABLISHED per specification
    causation_details = Column(Text, nullable=True)  # Explanation that causation is not established
    
    # Verification status (user-facing pattern validation)
    verification_status = Column(String, nullable=False, default="CANDIDATE")  # CANDIDATE, VERIFIED, REJECTED, UNCERTAIN, CONTRADICTED
    verification_timestamp = Column(DateTime(timezone=True), nullable=True)  # When verification status was last updated
    verified_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)  # Who verified/rejected the pattern
    
    # User response and corrections
    user_response = Column(Text, nullable=True)  # Caregiver's response when verifying (confirm/reject/not_sure/etc.)
    user_corrections = Column(JSONList, nullable=True)  # Corrections made by user (occurrence, date, entity, context, etc.)
    user_added_context = Column(Text, nullable=True)  # Context added by user that system couldn't know
    
    # Professional follow-up
    professional_follow_up_recommended = Column(Boolean, nullable=False, default=False)  # Whether pattern warrants professional discussion
    professional_follow_up_details = Column(Text, nullable=True)  # Details about recommended follow-up
    
    # Related system entities
    related_open_loop_id = Column(String, nullable=True, index=True)  # Reference to Open Loop if pattern created one
    related_decision_id = Column(String, nullable=True, index=True)   # Reference to Decision if pattern influenced one
    related_outcome_id = Column(String, nullable=True, index=True)   # Reference to Outcome if pattern relates to one
    
    # Provenance and audit trail
    provenance = Column(JSONDict, nullable=False)  # Full provenance chain: detection method, evidence sources, etc.
    detection_method = Column(String, nullable=False)  # How this pattern was detected (algorithm, heuristic, etc.)
    detection_timestamp = Column(DateTime(timezone=True), nullable=False, default=func.now())  # When pattern was first detected
    
    # Permissions and access control
    permissions = Column(JSONList, nullable=False)  # List of permissions required to access this pattern
    access_control_context = Column(JSONDict, nullable=True)  # Context for permission evaluation
    
    # Status and lifecycle
    is_active = Column(Boolean, nullable=False, default=True)
    lifecycle_status = Column(String, nullable=False, default="DETECTED")  # DETECTED, TRACKING, RESOLVED, SUPERSEDED, etc.
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    person = relationship("Person", foreign_keys=[person_id])
    verifier = relationship("Caregiver", foreign_keys=[verified_by_caregiver_id])
    
    __table_args__ = (
        Index("ix_pattern_person_category", "person_id", "category"),
        Index("ix_pattern_verification_status", "verification_status"),
        Index("ix_pattern_active", "is_active"),
        Index("ix_pattern_person_time", "person_id", "first_occurrence_time"),
    )
