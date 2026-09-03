from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, ForeignKey, Enum as SQLEnum
from sqlalchemy.sql import func
import uuid
from app.core.database import Base
from app.core.types import JSONList
from app.core.safety_enums import (
    SafetyDecision,
    RiskClass,
    EscalationDestination,
    EscalationStatus,
    SafetyState,
    SafetyEventType,
)


class SafetyDecisionModel(Base):
    __tablename__ = "safety_decisions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)

    decision = Column(SQLEnum(SafetyDecision), nullable=False)
    safety_state = Column(SQLEnum(SafetyState), nullable=False)
    risk_class = Column(SQLEnum(RiskClass), nullable=False)

    claim_verifications = Column(JSONList, nullable=True)
    qualifications = Column(JSONList, nullable=True)
    violations = Column(JSONList, nullable=True)

    escalation_required = Column(Boolean, nullable=False, default=False)
    escalation_destination = Column(SQLEnum(EscalationDestination), nullable=True)
    escalation_reason = Column(Text, nullable=True)

    human_review_required = Column(Boolean, nullable=False, default=False)

    rationale = Column(Text, nullable=False)
    policy_references = Column(JSONList, nullable=True)

    input_claims = Column(JSONList, nullable=True)
    evidence_ids = Column(JSONList, nullable=True)

    final_output = Column(Text, nullable=True)
    modified_output = Column(Text, nullable=True)

    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    safety_version = Column(String, nullable=False, default="1.0")

    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    superseded_by_decision_id = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)


class SafetyEventModel(Base):
    __tablename__ = "safety_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(SQLEnum(SafetyEventType), nullable=False)
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    subject_type = Column(String, nullable=False)
    subject_id = Column(String, nullable=False)

    description = Column(Text, nullable=False)
    safety_decision_id = Column(String, ForeignKey("safety_decisions.id"), nullable=True)
    escalation_id = Column(String, nullable=True)

    violations = Column(JSONList, nullable=True)
    policy_references = Column(JSONList, nullable=True)
    input_claims = Column(JSONList, nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    final_output = Column(Text, nullable=True)

    reviewer_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    override_reason = Column(Text, nullable=True)

    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)


class SafetyAuditRecordModel(Base):
    __tablename__ = "safety_audit_records"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    request_id = Column(String, nullable=False, unique=True, index=True)

    user_role = Column(String, nullable=False)
    input_text = Column(Text, nullable=False)

    candidate_claims = Column(JSONList, nullable=True)
    evidence_ids = Column(JSONList, nullable=True)
    source_quality = Column(JSONList, nullable=True)
    epistemic_states = Column(JSONList, nullable=True)
    contradictions = Column(JSONList, nullable=True)

    risk_classification = Column(SQLEnum(RiskClass), nullable=False)
    safety_decision = Column(SQLEnum(SafetyDecision), nullable=False)
    safety_state = Column(SQLEnum(SafetyState), nullable=False)

    qualifications = Column(JSONList, nullable=True)
    violations = Column(JSONList, nullable=True)
    escalation_id = Column(String, nullable=True)

    human_review_required = Column(Boolean, nullable=False, default=False)
    rationale = Column(Text, nullable=False)
    policy_references = Column(JSONList, nullable=True)

    final_output = Column(Text, nullable=True)
    model_version = Column(String, nullable=True)
    safety_version = Column(String, nullable=False, default="1.0")

    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    reviewer_id = Column(String, ForeignKey("caregivers.id"), nullable=True)
    override_reason = Column(Text, nullable=True)


class EscalationModel(Base):
    __tablename__ = "escalations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    affected_person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)

    trigger = Column(Text, nullable=False)
    risk_category = Column(SQLEnum(RiskClass), nullable=False)

    evidence_ids = Column(JSONList, nullable=True)
    uncertainty = Column(Text, nullable=True)

    recommended_reviewer = Column(SQLEnum(EscalationDestination), nullable=False)
    urgency = Column(String, nullable=False, default="routine")

    status = Column(SQLEnum(EscalationStatus), nullable=False, default=EscalationStatus.DETECTED)
    decision = Column(Text, nullable=True)
    reviewer_identity = Column(String, nullable=True)
    resolution = Column(Text, nullable=True)

    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    provenance = Column(Text, nullable=True)
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)


class SafetyPolicyRuleModel(Base):
    __tablename__ = "safety_policy_rules"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    rule_id = Column(String, nullable=False, unique=True, index=True)
    rule_name = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    is_deterministic = Column(Boolean, nullable=False, default=True)
    claim_types = Column(JSONList, nullable=True)
    risk_classes = Column(JSONList, nullable=True)

    action = Column(SQLEnum(SafetyDecision), nullable=False)
    conditions = Column(JSONList, nullable=True)
    priority = Column(String, nullable=False, default="100")

    is_active = Column(Boolean, nullable=False, default=True)
    version = Column(String, nullable=False, default="1.0")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by_caregiver_id = Column(String, ForeignKey("caregivers.id"), nullable=True)


class SafetyBenchmarkCaseModel(Base):
    __tablename__ = "safety_benchmark_cases"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    benchmark_category = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    input_text = Column(Text, nullable=False)
    expected_decision = Column(SQLEnum(SafetyDecision), nullable=False)
    expected_risk_class = Column(SQLEnum(RiskClass), nullable=False)

    expected_violations = Column(JSONList, nullable=True)
    expected_qualifications = Column(JSONList, nullable=True)

    tags = Column(JSONList, nullable=True)
    is_adversarial = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class SafetyBlindSpotModel(Base):
    __tablename__ = "safety_blind_spots"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)

    failure_mechanism = Column(Text, nullable=False)
    consequence = Column(Text, nullable=False)
    prevention = Column(Text, nullable=False)
    detection_method = Column(Text, nullable=False)

    test_case_id = Column(String, ForeignKey("safety_benchmark_cases.id"), nullable=True)
    is_mitigated = Column(Boolean, nullable=False, default=False)

    identified_at = Column(DateTime(timezone=True), server_default=func.now())
    mitigated_at = Column(DateTime(timezone=True), nullable=True)


class SafetyFeedbackModel(Base):
    __tablename__ = "safety_feedback"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    person_id = Column(String, ForeignKey("persons.id"), nullable=False, index=True)
    safety_event_id = Column(String, ForeignKey("safety_events.id"), nullable=False)

    feedback_type = Column(String, nullable=False)
    feedback_text = Column(Text, nullable=False)

    reviewer_id = Column(String, ForeignKey("caregivers.id"), nullable=False)
    original_decision = Column(SQLEnum(SafetyDecision), nullable=False)
    override_decision = Column(SQLEnum(SafetyDecision), nullable=True)

    is_correct = Column(Boolean, nullable=False)
    lesson_learned = Column(Text, nullable=True)

    timestamp = Column(DateTime(timezone=True), server_default=func.now())


class SafetyRegressionModel(Base):
    __tablename__ = "safety_regressions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    check_name = Column(String, nullable=False)
    previous_score = Column(Float, nullable=False)
    current_score = Column(Float, nullable=False)
    threshold = Column(Float, nullable=False)
    passed = Column(Boolean, nullable=False)
    regression_detected = Column(Boolean, nullable=False)
    details = Column(Text, nullable=True)

    model_version = Column(String, nullable=True)
    safety_version = Column(String, nullable=True)

    timestamp = Column(DateTime(timezone=True), server_default=func.now())
