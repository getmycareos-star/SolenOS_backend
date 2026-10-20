"""Service for identifying information-reducing actions based on care context.
Part II of SOLOS specification: Day 1 "What Should I Do?" Moments.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.models.evidence import Evidence as EvidenceModel
from app.models.open_loop import OpenLoop
from app.models.caregiver_intelligence import CaregiverResponsibility, ResponsibilityGap
from app.models.time_location import Appointment
from app.models.care_planning import CareDecision
from app.models.outcome import Outcome
from app.models.medication import Medication
from app.models.handoff import Handoff, HandoffQuestion
from app.models.follow_up import FollowUp
from app.models.contradiction import ContradictionAnalysis, ContradictionResolution
from app.models.care_event import CareEvent as CareEventModel
from app.models.identity import Person
from app.models.caregiver import Caregiver
from app.core.types import JSONList


class WhatShouldIDoService:
    """Service for identifying information-reducing actions from care context."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_information_reducing_actions(self, person_id: str) -> List[Dict[str, Any]]:
        """Identify information-reducing actions based on unresolved care context.
        
        Returns a list of suggested actions with metadata explaining why each
        action is recommended and what information it would resolve.
        """
        actions = []
        
        # Check for unknowns in evidence
        actions.extend(self._identify_evidence_unknowns(person_id))
        
        # Check for contradictions
        actions.extend(self._identify_contradictions(person_id))
        
        # Check for open loops/follow-ups needing attention
        actions.extend(self._identify_open_loops(person_id))
        
        # Check for unresolved responsibilities
        actions.extend(self._identify_responsibility_gaps(person_id))
        
        # Check for upcoming appointments needing preparation
        actions.extend(self._identify_appointment_preparation_needs(person_id))
        
        # Check for pending decisions
        actions.extend(self._identify_pending_decisions(person_id))
        
        # Check for unresolved outcomes
        actions.extend(self._identify_unresolved_outcomes(person_id))
        
        # Check for medication questions
        actions.extend(self._identify_medication_questions(person_id))
        
        # Check for handoff items needing attention
        actions.extend(self._identify_handoff_actions(person_id))
        
        # Prioritize actions based on urgency and importance
        actions = self._prioritize_actions(actions)
        
        return actions
    
    def _identify_evidence_unknowns(self, person_id: str) -> List[Dict[str, Any]]:
        """Identify unknown or uncertain evidence items that need clarification."""
        actions = []
        
        # Find evidence with uncertain status or unclear information
        uncertain_evidence = self.db.query(EvidenceModel).filter(
            and_(
                EvidenceModel.person_id == person_id,
                or_(
                    EvidenceModel.status == 'uncertain',
                    EvidenceModel.status == 'unresolved',
                    EvidenceModel.uncertainty_level.in_(['possible', 'unlikely', 'unknown']),
                    EvidenceModel.negation_status == 'uncertain'
                )
            )
        ).all()
        
        for evidence in uncertain_evidence:
            actions.append({
                'type': 'CLARIFY',
                'target_type': 'evidence',
                'target_id': evidence.id,
                'description': f"Clarify the meaning of evidence: {evidence.source_text[:100]}...",
                'why': "Evidence is marked as uncertain or unclear and needs caregiver verification",
                'what_needs_clarification': evidence.source_text,
                'who_can_help': self._determine_evidence_source(evidence),
                'what_information_is_missing': "Clear understanding of what this evidence means",
                'what_needs_follow_up': "Verification of evidence interpretation",
                'expected_outcome': "Verified evidence with clear interpretation",
                'related_evidence_ids': [evidence.id],
                'priority': 'medium'
            })
        
        return actions
    
    def _identify_contradictions(self, person_id: str) -> List[Dict[str, Any]]:
        """Identify unresolved contradictions that need clarification."""
        actions = []
        
        # Find unresolved contradiction analyses
        unresolved_contradictions = self.db.query(ContradictionAnalysis).filter(
            and_(
                ContradictionAnalysis.person_id == person_id,
                ContradictionAnalysis.is_active == True,
                ContradictionAnalysis.is_auto_resolvable == False  # Needs human input
            )
        ).all()
        
        for contradiction in unresolved_contradictions:
            actions.append({
                'type': 'CLARIFY',
                'target_type': 'contradiction',
                'target_id': contradiction.id,
                'description': f"Clarify contradiction: {contradiction.summary[:100]}...",
                'why': "Contradiction analysis indicates human judgment is needed to resolve conflicting information",
                'what_needs_clarification': contradiction.summary,
                'who_can_help': contradiction.who_can_resolve,
                'what_information_is_missing': "Resolution of conflicting evidence",
                'what_needs_follow_up': "Human judgment to resolve contradiction",
                'expected_outcome': "Resolved contradiction with clear understanding",
                'related_evidence_ids': contradiction.evidence_ids or [],
                'priority': 'high' if contradiction.primary_kind else 'medium'
            })
        
        return actions
    
    def _identify_open_loops(self, person_id: str) -> List[Dict[str, Any]]:
        """Identify open loops/follow-ups that need attention."""
        actions = []
        
        # Find active open loops that are not yet completed
        open_loops = self.db.query(OpenLoop).filter(
            and_(
                OpenLoop.person_id == person_id,
                OpenLoop.is_active == True,
                OpenLoop.status.in_(['scheduled', 'in_progress']),  # Not completed
                OpenLoop.due_at <= datetime.now(timezone.utc) + timedelta(days=7)  # Due within a week
            )
        ).all()
        
        for loop in open_loops:
            actions.append({
                'type': 'FOLLOW_UP',
                'target_type': 'open_loop',
                'target_id': loop.id,
                'description': f"Follow up on: {loop.what_to_recheck[:100]}...",
                'why': loop.why_open or "Open loop requires follow-up checking",
                'what_needs_clarification': loop.what_to_recheck,
                'who_can_help': self._determine_loop_owner(loop),
                'what_information_is_missing': loop.what_result_changes_the_plan,
                'what_needs_follow_up': "Checking for updates or resolution",
                'expected_outcome': "Resolution of open loop with new evidence",
                'related_evidence_ids': loop.evidence_ids or [],
                'priority': loop.priority_hint or 'medium',
                'due_at': loop.due_at.isoformat() if loop.due_at else None
            })
        
        return actions
    
    def _identify_responsibility_gaps(self, person_id: str) -> List[Dict[str, Any]]:
        """Identify gaps in responsibilities that need assignment or clarification."""
        actions = []
        
        # Find responsibility gaps that are still open
        responsibility_gaps = self.db.query(ResponsibilityGap).filter(
            and_(
                ResponsibilityGap.person_id == person_id,
                ResponsibilityGap.status == 'open'  # Assuming CoverageGapStatus.OPEN = 'open'
            )
        ).all()
        
        for gap in responsibility_gaps:
            actions.append({
                'type': 'ASSIGN',
                'target_type': 'responsibility_gap',
                'target_id': gap.id,
                'description': f"Assign responsibility for: {gap.responsibility_type}",
                'why': "Responsibility gap detected - no caregiver assigned to this responsibility",
                'what_needs_clarification': f"Who should be responsible for {gap.responsibility_type}",
                'who_can_help': "Primary caregiver or care coordinator",
                'what_information_is_missing': "Clear assignment of responsibility",
                'what_needs_follow_up': "Assignment and acceptance of responsibility",
                'expected_outcome': "Clearly assigned responsibility with acceptance",
                'related_evidence_ids': [],
                'priority': 'high'
            })
        
        return actions
    
    def _identify_appointment_preparation_needs(self, person_id: str) -> List[Dict[str, Any]]:
        """Identify upcoming appointments that need preparation."""
        actions = []
        
        # Find upcoming appointments within the next 7 days
        upcoming_appointments = self.db.query(Appointment).filter(
            and_(
                Appointment.person_id == person_id,
                Appointment.scheduled_at >= datetime.now(timezone.utc),
                Appointment.scheduled_at <= datetime.now(timezone.utc) + timedelta(days=7),
                Appointment.status == 'scheduled'
            )
        ).all()
        
        for appointment in upcoming_appointments:
            # Check if preparation is needed
            needs_preparation = bool(
                appointment.medication_questions or
                appointment.documents_to_bring or
                appointment.preparation_notes
            )
            
            if needs_preparation:
                actions.append({
                    'type': 'PREPARE',
                    'target_type': 'appointment',
                    'target_id': appointment.id,
                    'description': f"Prepare for appointment: {appointment.title}",
                    'why': "Upcoming appointment requires preparation of information or questions",
                    'what_needs_clarification': "What information to bring and what questions to ask",
                    'who_can_help': "Primary caregiver",
                    'what_information_is_missing': "Preparation notes, documents to bring, medication questions",
                    'what_needs_follow_up': "Gathering required information and documents",
                    'expected_outcome': "Prepared for appointment with all necessary information",
                    'related_evidence_ids': appointment.evidence_ids or [],
                    'priority': 'high',
                    'appointment_date': appointment.scheduled_at.isoformat() if appointment.scheduled_at else None
                })
        
        return actions
    
    def _identify_pending_decisions(self, person_id: str) -> List[Dict[str, Any]]:
        """Identify decisions that need to be made or await outcome."""
        actions = []
        
        # Find decisions that are proposed but not yet decided
        pending_decisions = self.db.query(CareDecision).filter(
            and_(
                CareDecision.person_id == person_id,
                CareDecision.status == 'proposed',  # Assuming DecisionStatus.PROPOSED = 'proposed'
                CareDecision.is_active == True
            )
        ).all()
        
        for decision in pending_decisions:
            actions.append({
                'type': 'CLARIFY',
                'target_type': 'decision',
                'target_id': decision.id,
                'description': f"Clarify decision: {decision.question[:100]}...",
                'why': "Decision has been proposed but not yet made",
                'what_needs_clarification': decision.question,
                'who_can_help': decision.owner.value if hasattr(decision.owner, 'value') else str(decision.owner),
                'what_information_is_missing': "Decision outcome based on available options",
                'what_needs_follow_up': "Making the decision and recording outcome",
                'expected_outcome': "Decided course of action with rationale",
                'related_evidence_ids': decision.evidence_ids or [],
                'priority': 'high' if decision.requires_clinician else 'medium'
            })
        
        return actions
    
    def _identify_unresolved_outcomes(self, person_id: str) -> List[Dict[str, Any]]:
        """Identify outcomes that need to be recorded or verified."""
        actions = []
        
        # Find outcomes with unknown status or pending verification
        unresolved_outcomes = self.db.query(Outcome).filter(
            and_(
                Outcome.person_id == person_id,
                or_(
                    Outcome.outcome_status == 'unknown',  # Assuming OutcomeStatus.UNKNOWN = 'unknown'
                    # Note: There's no explicit pending_verification status in the model
                    # We'll just check for unknown outcomes for now
                )
            )
        ).all()
        
        for outcome in unresolved_outcomes:
            actions.append({
                'type': 'VERIFY',
                'target_type': 'outcome',
                'target_id': outcome.id,
                'description': f"Verify outcome: {outcome.expectation_description[:100]}...",
                'why': "Outcome expectation recorded but actual outcome not yet verified",
                'what_needs_clarification': outcome.expectation_description,
                'who_can_help': "Primary caregiver",
                'what_information_is_missing': "Actual outcome and whether it matched expectations",
                'what_needs_follow_up': "Recording actual outcome and comparing to expectation",
                'expected_outcome': "Verified outcome with comparison to expectation",
                'related_evidence_ids': outcome.evidence_ids or [],
                'priority': 'medium'
            })
        
        return actions
    
    def _identify_medication_questions(self, person_id: str) -> List[Dict[str, Any]]:
        """Identify medication-related questions or uncertainties."""
        actions = []
        
        # Find medications with verification questions or unclear instructions
        medications_with_questions = self.db.query(Medication).filter(
            and_(
                Medication.person_id == person_id,
                Medication.is_active == True,
                or_(
                    Medication.open_loop_id.isnot(None),  # Has linked open loop for confirmation
                    Medication.verification_status.in_(['unverified', 'conflicting', 'unknown']),  # Assuming VERIFICATION_STATES
                    Medication.change_type.isnot(None)  # Recent change needing verification
                )
            )
        ).all()
        
        for medication in medications_with_questions:
            if medication.open_loop_id:
                # Linked to an open loop
                actions.append({
                    'type': 'VERIFY',
                    'target_type': 'medication',
                    'target_id': medication.id,
                    'description': f"Verify medication instruction: {medication.medication_name}",
                    'why': "Medication has an open loop indicating confirmation needed",
                    'what_needs_clarification': f"Current correct instruction for {medication.medication_name}",
                    'who_can_help': self._determine_medication_source(medication),
                    'what_information_is_missing': "Clear, current medication instruction",
                    'what_needs_follow_up': "Verification with appropriate medical source",
                    'expected_outcome': "Verified and confirmed medication instruction",
                    'related_evidence_ids': [medication.evidence_id] if medication.evidence_id else [],
                    'priority': 'high'
                })
            else:
                # General medication verification needed
                actions.append({
                    'type': 'VERIFY',
                    'target_type': 'medication',
                    'target_id': medication.id,
                    'description': f"Verify medication: {medication.medication_name}",
                    'why': f"Medication verification status is {medication.verification_status}",
                    'what_needs_clarification': f"Current status and instructions for {medication.medication_name}",
                    'who_can_help': self._determine_medication_source(medication),
                    'what_information_is_missing': "Clear verification of medication details",
                    'what_needs_follow_up': "Verification with appropriate source",
                    'expected_outcome': "Verified medication information",
                    'related_evidence_ids': [medication.evidence_id] if medication.evidence_id else [],
                    'priority': 'medium'
                })
        
        return actions
    
    def _identify_handoff_actions(self, person_id: str) -> List[Dict[str, Any]]:
        """Identify handoff-related actions that need attention."""
        actions = []
        
        # Find handoff questions that need answers
        handoff_questions = self.db.query(HandoffQuestion).join(Handoff).filter(
            and_(
                Handoff.person_id == person_id,
                HandoffQuestion.status == 'asked',  # Assuming QuestionStatus.ASKED = 'asked'
                HandoffQuestion.answered_at.is_(None)  # Not yet answered
            )
        ).all()
        
        for question in handoff_questions:
            actions.append({
                'type': 'DOCUMENT',
                'target_type': 'handoff_question',
                'target_id': question.id,
                'description': f"Answer handoff question: {question.question_text[:100]}...",
                'why': "Handoff question asked but not yet answered",
                'what_needs_clarification': question.question_text,
                'who_can_help': "Primary caregiver or relevant party",
                'what_information_is_missing': "Answer to the handoff question",
                'what_needs_follow_up': "Providing answer to handoff question",
                'expected_outcome': "Answered handoff question with relevant information",
                'related_evidence_ids': [],
                'priority': 'medium'
            })
        
        return actions
    
    def _determine_evidence_source(self, evidence: EvidenceModel) -> Optional[str]:
        """Determine who can help clarify evidence based on its source."""
        if evidence.source_type:
            source_mapping = {
                'manual_caregiver_input': "The caregiver who provided the information",
                'manual_care_recipient_input': "The care recipient",
                'manual_provider_input': "The healthcare provider who gave the information",
                'document_pdf': "The document source",
                'document_txt': "The document source",
                'email': "The sender of the email",
                'voice_transcript': "The speaker in the recording",
                'device_reading': "The device manufacturer or healthcare provider",
                'system_generated': "The system that generated this information"
            }
            return source_mapping.get(evidence.source_type, "Appropriate source based on evidence type")
        return None
    
    def _determine_loop_owner(self, open_loop: OpenLoop) -> Optional[str]:
        """Determine who owns or should handle an open loop."""
        if open_loop.completed_by_caregiver_id:
            return "Caregiver who previously worked on this loop"
        elif open_loop.subject_id:
            return f"Caregiver responsible for {open_loop.subject_type}"
        return "Primary caregiver"
    
    def _determine_medication_source(self, medication: Medication) -> Optional[str]:
        """Determine who can help clarify medication information."""
        source_mapping = {
            'physician': "Prescribing physician",
            'nurse_practitioner': "Nurse practitioner",
            'pharmacist': "Pharmacist",
            'nurse': "Nurse",
            'caregiver_report': "Caregiver who reported the information",
            'patient_report': "Care recipient",
            'medication_record': "Medication record or prescription",
            'discharge_instruction': "Discharge documentation from healthcare facility"
        }
        return source_mapping.get(medication.source_type, "Appropriate healthcare source")
    
    def _prioritize_actions(self, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Prioritize actions based on urgency, importance, and deadlines."""
        # Define priority weights
        priority_weights = {
            'critical': 4,
            'high': 3,
            'medium': 2,
            'low': 1
        }
        
        # Sort by priority (descending), then by due date if available
        def action_priority(action):
            priority_weight = priority_weights.get(action.get('priority', 'low'), 1)
            # Boost priority for items with imminent due dates
            due_date_boost = 0
            if 'due_at' in action and action['due_at']:
                try:
                    due_at = datetime.fromisoformat(action['due_at'])
                    hours_until_due = (due_at - datetime.now(timezone.utc)).total_seconds() / 3600
                    if hours_until_due <= 24:  # Due within 24 hours
                        due_date_boost = 2
                    elif hours_until_due <= 72:  # Due within 3 days
                        due_date_boost = 1
                except:
                    pass
            return -(priority_weight + due_date_boost)  # Negative for descending sort
        
        return sorted(actions, key=action_priority)


def get_what_should_i_do_actions(db: Session, person_id: str) -> List[Dict[str, Any]]:
    """Convenience function to get information-reducing actions for a person."""
    service = WhatShouldIDoService(db)
    return service.get_information_reducing_actions(person_id)
