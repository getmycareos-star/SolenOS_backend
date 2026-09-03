from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from app.core.database import get_db
from app.schemas.safety import (
    SafetyContractInput,
    SafetyContractOutput,
    SafetyDecisionObject,
    SafetyEvent,
    SafetyAuditRecord,
    EscalationObject,
    SafetyBenchmarkCase,
    SafetyEvaluationResult,
    SafetyBlindSpot,
    SafetyFeedback,
    SafetyRegressionCheck,
    SafetyDecision,
    RiskClass,
    ClaimType,
    EscalationDestination,
    EscalationStatus,
    SafetyInvariantViolation,
    SafetyEventType,
    SafetyPolicyRule,
)
from app.core.safety_enums import (
    EscalationStatus as EscalationStatusEnum,
)
from app.models.safety import (
    SafetyEventModel,
    SafetyAuditRecordModel,
    EscalationModel,
    SafetyPolicyRuleModel,
    SafetyBenchmarkCaseModel,
    SafetyBlindSpotModel,
    SafetyFeedbackModel,
)
from app.services.safety import SafetyEngine
from app.services.risk_escalation import EscalationEngine
from app.services.safety_evaluation import SafetyEvaluationSuite
import uuid

router = APIRouter()


@router.post("/evaluate", response_model=SafetyContractOutput)
def evaluate_safety(input_data: SafetyContractInput, db: Session = Depends(get_db)):
    engine = SafetyEngine()
    output = engine.evaluate(input_data)
    return output


@router.post("/validate-response", response_model=SafetyDecisionObject)
def validate_response(
    candidate_response: str,
    claims: List[str],
    evidence_ids: List[str],
    risk_class: RiskClass,
    db: Session = Depends(get_db),
):
    engine = SafetyEngine()
    decision = engine.validate_response(candidate_response, claims, evidence_ids, risk_class)
    return decision


@router.post("/audit", response_model=SafetyAuditRecord)
def create_safety_audit(input_data: SafetyContractInput, output: SafetyContractOutput, final_output: Optional[str] = None, db: Session = Depends(get_db)):
    engine = SafetyEngine()
    audit_data = engine.create_safety_audit(input_data, output, final_output)
    db_audit = SafetyAuditRecordModel(**audit_data)
    db.add(db_audit)
    db.commit()
    db.refresh(db_audit)
    return db_audit


@router.get("/audit/{person_id}", response_model=List[SafetyAuditRecord])
def list_safety_audits(person_id: str, limit: int = 50, db: Session = Depends(get_db)):
    return (
        db.query(SafetyAuditRecordModel)
        .filter(SafetyAuditRecordModel.person_id == person_id)
        .order_by(SafetyAuditRecordModel.timestamp.desc())
        .limit(limit)
        .all()
    )


@router.post("/escalations", response_model=EscalationObject)
def create_escalation(
    person_id: str,
    affected_person_id: str,
    trigger: str,
    risk_category: RiskClass,
    recommended_reviewer: EscalationDestination,
    urgency: str = "routine",
    evidence_ids: Optional[List[str]] = None,
    uncertainty: Optional[str] = None,
    provenance: Optional[str] = None,
    db: Session = Depends(get_db),
):
    escalation_engine = EscalationEngine()
    escalation_data = escalation_engine.determine_escalation(risk_category, trigger, person_id)

    db_escalation = EscalationModel(
        id=str(uuid.uuid4()),
        person_id=person_id,
        affected_person_id=affected_person_id,
        trigger=trigger,
        risk_category=risk_category,
        evidence_ids=evidence_ids or [],
        uncertainty=uncertainty,
        recommended_reviewer=escalation_data["destination"],
        urgency=escalation_data["urgency"],
        status=EscalationStatusEnum.DETECTED,
        timestamp=datetime.now(timezone.utc),
        provenance=provenance,
    )
    db.add(db_escalation)
    db.commit()
    db.refresh(db_escalation)
    return db_escalation


@router.get("/escalations/{person_id}", response_model=List[EscalationObject])
def list_escalations(person_id: str, status: Optional[EscalationStatus] = None, db: Session = Depends(get_db)):
    query = db.query(EscalationModel).filter(EscalationModel.person_id == person_id)
    if status:
        query = query.filter(EscalationModel.status == status)
    return query.order_by(EscalationModel.timestamp.desc()).all()


@router.patch("/escalations/{escalation_id}")
def update_escalation(
    escalation_id: str,
    status: EscalationStatus,
    decision: Optional[str] = None,
    reviewer_identity: Optional[str] = None,
    resolution: Optional[str] = None,
    db: Session = Depends(get_db),
):
    db_escalation = db.query(EscalationModel).filter(EscalationModel.id == escalation_id).first()
    if not db_escalation:
        raise HTTPException(status_code=404, detail="Escalation not found")

    db_escalation.status = status
    if decision:
        db_escalation.decision = decision
    if reviewer_identity:
        db_escalation.reviewer_identity = reviewer_identity
    if resolution:
        db_escalation.resolution = resolution
    if status in (EscalationStatusEnum.RESOLVED, EscalationStatusEnum.DISMISSED):
        db_escalation.resolved_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(db_escalation)
    return db_escalation


@router.post("/events", response_model=SafetyEvent)
def create_safety_event(
    event_type: SafetyEventType,
    person_id: str,
    subject_type: str,
    subject_id: str,
    description: str,
    safety_decision_id: Optional[str] = None,
    escalation_id: Optional[str] = None,
    violations: Optional[List[SafetyInvariantViolation]] = None,
    policy_references: Optional[List[str]] = None,
    input_claims: Optional[List[str]] = None,
    evidence_ids: Optional[List[str]] = None,
    final_output: Optional[str] = None,
    reviewer_id: Optional[str] = None,
    override_reason: Optional[str] = None,
    created_by_caregiver_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    db_event = SafetyEventModel(
        id=str(uuid.uuid4()),
        event_type=event_type,
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        description=description,
        safety_decision_id=safety_decision_id,
        escalation_id=escalation_id,
        violations=[v.value for v in violations] if violations else [],
        policy_references=policy_references or [],
        input_claims=input_claims or [],
        evidence_ids=evidence_ids or [],
        final_output=final_output,
        reviewer_id=reviewer_id,
        override_reason=override_reason,
        timestamp=datetime.now(timezone.utc),
        created_by_caregiver_id=created_by_caregiver_id,
    )
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event


@router.get("/events/{person_id}", response_model=List[SafetyEvent])
def list_safety_events(person_id: str, limit: int = 50, db: Session = Depends(get_db)):
    return (
        db.query(SafetyEventModel)
        .filter(SafetyEventModel.person_id == person_id)
        .order_by(SafetyEventModel.timestamp.desc())
        .limit(limit)
        .all()
    )


@router.post("/policy-rules", response_model=SafetyPolicyRule)
def create_policy_rule(
    rule_id: str,
    rule_name: str,
    description: str,
    action: SafetyDecision,
    is_deterministic: bool = True,
    claim_types: Optional[List[ClaimType]] = None,
    risk_classes: Optional[List[RiskClass]] = None,
    conditions: Optional[Dict[str, Any]] = None,
    priority: int = 100,
    created_by_caregiver_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    db_rule = SafetyPolicyRuleModel(
        id=str(uuid.uuid4()),
        rule_id=rule_id,
        rule_name=rule_name,
        description=description,
        is_deterministic=is_deterministic,
        claim_types=[ct.value for ct in claim_types] if claim_types else [],
        risk_classes=[rc.value for rc in risk_classes] if risk_classes else [],
        action=action,
        conditions=conditions or {},
        priority=str(priority),
        is_active=True,
        version="1.0",
        created_at=datetime.now(timezone.utc),
        created_by_caregiver_id=created_by_caregiver_id,
    )
    db.add(db_rule)
    db.commit()
    db.refresh(db_rule)
    return db_rule


@router.get("/policy-rules", response_model=List[SafetyPolicyRule])
def list_policy_rules(is_active: bool = True, db: Session = Depends(get_db)):
    return db.query(SafetyPolicyRuleModel).filter(SafetyPolicyRuleModel.is_active == is_active).all()


@router.post("/benchmark-cases", response_model=SafetyBenchmarkCase)
def create_benchmark_case(
    benchmark_category: str,
    name: str,
    input_text: str,
    expected_decision: SafetyDecision,
    expected_risk_class: RiskClass,
    description: Optional[str] = None,
    expected_violations: Optional[List[SafetyInvariantViolation]] = None,
    expected_qualifications: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    is_adversarial: bool = False,
    db: Session = Depends(get_db),
):
    db_case = SafetyBenchmarkCaseModel(
        id=str(uuid.uuid4()),
        benchmark_category=benchmark_category,
        name=name,
        description=description,
        input_text=input_text,
        expected_decision=expected_decision,
        expected_risk_class=expected_risk_class,
        expected_violations=[v.value for v in expected_violations] if expected_violations else [],
        expected_qualifications=expected_qualifications or [],
        tags=tags or [],
        is_adversarial=is_adversarial,
        created_at=datetime.now(timezone.utc),
    )
    db.add(db_case)
    db.commit()
    db.refresh(db_case)
    return db_case


@router.get("/benchmark-cases", response_model=List[SafetyBenchmarkCase])
def list_benchmark_cases(benchmark_category: Optional[str] = None, is_adversarial: Optional[bool] = None, db: Session = Depends(get_db)):
    query = db.query(SafetyBenchmarkCaseModel)
    if benchmark_category:
        query = query.filter(SafetyBenchmarkCaseModel.benchmark_category == benchmark_category)
    if is_adversarial is not None:
        query = query.filter(SafetyBenchmarkCaseModel.is_adversarial == is_adversarial)
    return query.order_by(SafetyBenchmarkCaseModel.created_at.desc()).all()


@router.post("/evaluate-benchmark", response_model=SafetyEvaluationResult)
def evaluate_benchmark(benchmark_category: Optional[str] = None, db: Session = Depends(get_db)):
    suite = SafetyEvaluationSuite()
    query = db.query(SafetyBenchmarkCaseModel)
    if benchmark_category:
        query = query.filter(SafetyBenchmarkCaseModel.benchmark_category == benchmark_category)
    cases = query.all()
    benchmark_cases = [
        SafetyBenchmarkCase(
            id=c.id,
            benchmark_category=c.benchmark_category,
            name=c.name,
            description=c.description or "",
            input_text=c.input_text,
            expected_decision=c.expected_decision,
            expected_risk_class=c.expected_risk_class,
            expected_violations=[SafetyInvariantViolation(v) for v in (c.expected_violations or [])],
            expected_qualifications=c.expected_qualifications or [],
            tags=c.tags or [],
            is_adversarial=c.is_adversarial,
            created_at=c.created_at,
        )
        for c in cases
    ]
    result = suite.run_benchmark(benchmark_cases)
    return result


@router.post("/adversarial-tests")
def run_adversarial_tests(db: Session = Depends(get_db)):
    suite = SafetyEvaluationSuite()
    return suite.run_adversarial_tests()


@router.get("/blind-spots", response_model=List[SafetyBlindSpot])
def list_blind_spots(is_mitigated: Optional[bool] = None, db: Session = Depends(get_db)):
    query = db.query(SafetyBlindSpotModel)
    if is_mitigated is not None:
        query = query.filter(SafetyBlindSpotModel.is_mitigated == is_mitigated)
    return query.order_by(SafetyBlindSpotModel.identified_at.desc()).all()


@router.post("/feedback", response_model=SafetyFeedback)
def create_safety_feedback(
    person_id: str,
    safety_event_id: str,
    feedback_type: str,
    feedback_text: str,
    reviewer_id: str,
    original_decision: SafetyDecision,
    override_decision: Optional[SafetyDecision] = None,
    is_correct: bool = False,
    lesson_learned: Optional[str] = None,
    db: Session = Depends(get_db),
):
    db_feedback = SafetyFeedbackModel(
        id=str(uuid.uuid4()),
        person_id=person_id,
        safety_event_id=safety_event_id,
        feedback_type=feedback_type,
        feedback_text=feedback_text,
        reviewer_id=reviewer_id,
        original_decision=original_decision,
        override_decision=override_decision,
        is_correct=is_correct,
        lesson_learned=lesson_learned,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(db_feedback)
    db.commit()
    db.refresh(db_feedback)
    return db_feedback


@router.get("/feedback/{person_id}", response_model=List[SafetyFeedback])
def list_safety_feedback(person_id: str, limit: int = 50, db: Session = Depends(get_db)):
    return (
        db.query(SafetyFeedbackModel)
        .filter(SafetyFeedbackModel.person_id == person_id)
        .order_by(SafetyFeedbackModel.timestamp.desc())
        .limit(limit)
        .all()
    )


@router.post("/regression-check", response_model=List[SafetyRegressionCheck])
def run_regression_check(previous_results: List[Dict[str, Any]], current_results: List[Dict[str, Any]], threshold: float = 0.95):
    suite = SafetyEvaluationSuite()
    return suite.run_regression_check(current_results, previous_results, threshold)


@router.get("/metrics/{person_id}")
def get_safety_metrics(person_id: str, db: Session = Depends(get_db)):
    events = db.query(SafetyEventModel).filter(SafetyEventModel.person_id == person_id).all()
    total = len(events)
    violations_count = sum(1 for e in events if e.violations)
    escalation_count = sum(1 for e in events if e.escalation_id)

    return {
        "person_id": person_id,
        "total_safety_events": total,
        "violations_detected": violations_count,
        "escalations_created": escalation_count,
        "violation_rate": violations_count / total if total > 0 else 0.0,
        "escalation_rate": escalation_count / total if total > 0 else 0.0,
    }
