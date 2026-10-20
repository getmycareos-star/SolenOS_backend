"""AI Validation Framework for SolenOS.

Provides a unified interface for validating AI-generated outputs against safety invariants and policies.
"""

from typing import Optional, List, Dict, Any
from app.services.safety import SafetyEvaluator, SafetyContractInput, SafetyContractOutput
from app.models.identity import Account
from app.core.database import Session


class AIValidationFramework:
    """Framework for validating AI-generated outputs."""

    def __init__(self, db_session: Session):
        """Initialize the framework with a database session.
        
        Args:
            db_session: SQLAlchemy database session
        """
        self.db = db_session
        self.safety_evaluator = SafetyEvaluator(db_session)

    def validate_ai_output(
        self,
        user_intent: str,
        evidence_references: List[Any],
        epistemic_status: Dict[str, Any],
        contradictions: List[Any],
        user_role: str = "caregiver",
        person_id: Optional[str] = None,
        account: Optional[Account] = None
    ) -> Dict[str, Any]:
        """Validate AI-generated output against safety invariants.
        
        Args:
            user_intent: The raw input/text from the user
            evidence_references: List of evidence references used in generating the output
            epistemic_status: Epistemic status of the AI's knowledge
            contradictions: List of contradictions detected in the reasoning
            user_role: Role of the user making the request (default: caregiver)
            person_id: ID of the person the request is about (optional)
            account: Account object of the user making the request (optional)
            
        Returns:
            Dictionary containing validation results including:
            - is_safe: Boolean indicating if output is safe
            - safety_decision: The safety decision (ALLOW, REFUSE, etc.)
            - safety_state: The safety state (GROUNDED, QUALIFIED, etc.)
            - rationale: Explanation of the decision
            - violations: List of safety invariant violations
            - qualifications: List of qualifications to the output
            - policy_references: List of policy references used
        """
        # Create safety contract input
        input_data = SafetyContractInput(
            person_id=person_id or "",
            user_intent=user_intent,
            evidence_references=evidence_references,
            epistemic_status=epistemic_status,
            contradictions=contradictions,
            user_role=user_role
        )
        
        # Evaluate against safety invariants
        safety_output: SafetyContractOutput = self.safety_evaluator.check_invariants(input_data)
        
        # Prepare validation result
        validation_result = {
            "is_safe": safety_output.decision.value in ["ALLOW", "ALLOW_WITH_QUALIFICATION"],
            "safety_decision": safety_output.decision.value,
            "safety_state": safety_output.safety_state.value,
            "rationale": safety_output.rationale,
            "violations": [v.value for v in safety_output.violations],
            "qualifications": [q.model_dump() for q in safety_output.qualifications],
            "policy_references": safety_output.policy_references,
            "safety_version": safety_output.safety_version,
            "timestamp": safety_output.timestamp.isoformat() if safety_output.timestamp else None
        }
        
        return validation_result

    def validate_and_audit(
        self,
        user_intent: str,
        evidence_references: List[Any],
        epistemic_status: Dict[str, Any],
        contradictions: List[Any],
        user_role: str = "caregiver",
        person_id: Optional[str] = None,
        account: Optional[Account] = None,
        reviewer_id: Optional[str] = None,
        override_reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Validate AI output and create an audit record.
        
        This method combines validation with audit record creation for tamper-proof tracking.
        
        Args:
            Same as validate_ai_output plus:
            reviewer_id: ID of the reviewer (caregiver) who reviewed the output
            override_reason: Reason for any override (if applicable)
            
        Returns:
            Dictionary containing both validation results and audit record information
        """
        # Perform validation
        validation_result = self.validate_ai_output(
            user_intent=user_intent,
            evidence_references=evidence_references,
            epistemic_status=epistemic_status,
            contradictions=contradictions,
            user_role=user_role,
            person_id=person_id,
            account=account
        )
        
        # Create audit record if we have a database session and necessary info
        audit_record_id = None
        if self.db and person_id:
            from app.models.safety import SafetyAuditRecordModel
            import uuid
            from datetime import datetime, timezone
            
            audit_record = SafetyAuditRecordModel(
                id=str(uuid.uuid4()),
                person_id=person_id,
                request_id=str(uuid.uuid4()),  # Generate a request ID
                user_role=user_role,
                input_text=user_intent,
                evidence_references=evidence_references,
                epistemic_status=epistemic_status,
                contradictions=contradictions,
                safety_decision=validation_result["safety_decision"],
                safety_state=validation_result["safety_state"],
                qualifications=validation_result["qualifications"],
                violations=validation_result["violations"],
                policy_references=validation_result["policy_references"],
                rationale=validation_result["rationale"],
                safety_version=validation_result["safety_version"],
                timestamp=datetime.now(timezone.utc),
                reviewer_id=reviewer_id,
                override_reason=override_reason
            )
            
            # Compute and set content hash for tamper-proofing
            audit_record.content_hash = audit_record.compute_content_hash()
            audit_record.previous_hash = SafetyAuditRecordModel.get_latest_hash(self.db)
            
            self.db.add(audit_record)
            self.db.commit()
            self.db.refresh(audit_record)
            audit_record_id = audit_record.id
        
        # Add audit record info to validation result
        validation_result["audit_record_id"] = audit_record_id
        
        return validation_result