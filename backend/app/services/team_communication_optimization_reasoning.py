"""Service for optimizing team communication using AI-assisted reasoning.
This service follows SolenOS's strict safety patterns: it only provides suggestions
that must pass through the TrustGuard and always falls back to deterministic logic.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.llm_enums import LLMTask, LLMReasoningMode
from app.models.identity import Person
from app.models.conversation import Conversation as ConversationModel
from app.models.evidence import Evidence as EvidenceModel
from app.models.care_event import CareEvent as CareEventModel
from app.models.outcome import Outcome as OutcomeModel
from app.services.llm import LocalReasoningEngine, LLMReasoningResult


class TeamCommunicationOptimizationReasoning:
    """Optimizes team communication using AI-assisted reasoning (summarization, translation, etc.).
    
    This service is designed to be used via LocalReasoningEngine which provides:
    - Circuit breaker protection
    - Audit logging
    - Fallback to deterministic answers
    - TrustGuard validation of all outputs
    """
    
    def __init__(self, db: Session, llm_engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self._llm_engine = llm_engine or LocalReasoningEngine(db)
        
    def reason(
        self,
        task: LLMTask,
        person_id: str,
        payload: Dict[str, Any],
        evidence_ids: List[str],
        subject_type: Optional[str],
        subject_id: Optional[str],
        mode: LLMReasoningMode,
        allow_diagnostic: bool,
        allow_medical_action: bool,
        require_evidence: bool,
    ) -> Optional[LLMReasoningResult]:
        """Main reasoning method called by LocalReasoningEngine.
        
        Returns None if:
        - The task is not CONVERSATION_REPLY (delegates to other services)
        - LLM is unavailable 
        - Output fails TrustGuard validation
        - Insufficient data for analysis
        """
        # Only handle our specific task - defer to engine for others
        if task != LLMTask.CONVERSATION_REPLY:
            return None
            
        # Extract conversation ID or parameters
        conversation_id = payload.get("conversation_id")
        # If not provided, we might look for recent conversations, but for now require it
        if not conversation_id:
            return None
            
        # Verify person exists
        try:
            person = self.db.query(Person).filter(Person.id == person_id).first()
            if not person:
                return None
        except Exception:
            # Database error - fail safe
            return None
            
        # Verify conversation exists and is linked to the person
        try:
            conversation = self.db.query(ConversationModel).filter(
                ConversationModel.id == conversation_id,
                ConversationModel.person_id == person_id
            ).first()
            if not conversation:
                # Conversation not found or not linked to person
                return None
        except Exception:
            return None
            
        # Gather relevant contextual data for communication analysis
        try:
            # Get evidence related to the conversation
            conversation_evidence = []
            if evidence_ids:
                conversation_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.id.in_(evidence_ids),
                    EvidenceModel.person_id == person_id
                ).all()
            else:
                # If no specific evidence IDs, get evidence associated with the conversation
                conversation_evidence = self.db.query(EvidenceModel).filter(
                    EvidenceModel.person_id == person_id,
                    EvidenceModel.conversation_id == conversation_id  # Assuming there's a conversation_id foreign key
                ).all()
                
            # Get care events around the conversation time
            conversation_time = conversation.created_at if hasattr(conversation, 'created_at') else None
            time_window_hours = 24  # Look 24 hours before and after
            start_time = conversation_time - timedelta(hours=time_window_hours) if conversation_time else None
            end_time = conversation_time + timedelta(hours=time_window_hours) if conversation_time else None
            
            related_events = []
            if start_time and end_time:
                related_events = self.db.query(CareEventModel).filter(
                    CareEventModel.person_id == person_id,
                    CareEventModel.occurred_at >= start_time,
                    CareEventModel.occurred_at <= end_time
                ).all()
                
            # Get outcomes related to the conversation
            related_outcomes = self.db.query(OutcomeModel).filter(
                OutcomeModel.person_id == person_id,
                OutcomeModel.recorded_at >= start_time if start_time else None,
                OutcomeModel.recorded_at <= end_time if end_time else None
            ).all() if start_time and end_time else []
                
        except Exception:
            # If we can't gather context, still proceed with what we have
            conversation_evidence = []
            related_events = []
            related_outcomes = []
            
        # Prepare payload for LLM
        llm_payload = {
            "person_id": person_id,
            "conversation_id": conversation_id,
            "conversation_info": {
                "id": conversation.id,
                "title": getattr(conversation, 'title', None),
                "description": getattr(conversation, 'description', None),
                "conversation_type": getattr(conversation, 'conversation_type', None),
                "participants": getattr(conversation, 'participants', []),
                "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
                "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
            },
            "conversation_evidence": [
                {
                    "id": e.id,
                    "type": e.type,
                    "source_text": e.source_text[:500] if e.source_text else None,  # More text for summarization/translation
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in conversation_evidence
            ],
            "related_events": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "title": e.title,
                    "description": e.description,
                    "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                }
                for e in related_events
            ],
            "related_outcomes": [
                {
                    "id": o.id,
                    "expectation_description": o.expectation_description,
                    "actual_outcome_description": o.actual_outcome_description,
                    "outcome_status": o.outcome_status,
                    "comparison_result": o.comparison_result,
                    "recorded_at": o.recorded_at.isoformat() if o.recorded_at else None,
                }
                for o in related_outcomes
            ],
            "context": payload.get("context", {}),
            "communication_goals": payload.get("communication_goals", [
                "summarize_key_information",
                "translate_to_caregiver_language",
                "highlight_action_items",
                "identify_open_questions",
                "ensure_clarity_and_conciseness"
            ]),
            "target_audience": payload.get("target_audience", "care_team"),  # care_team, caregiver, specialist, etc.
            "max_output_length": payload.get("max_output_length", 200),  # words or characters
        }
        
        # Use the LLM engine to get reasoning result
        # This will go through: LocalReasoningEngine -> OllamaClient -> TrustGuard
        try:
            result = self._llm_engine.reason(
                task=LLMTask.CONVERSATION_REPLY,
                person_id=person_id,
                payload=llm_payload,
                evidence_ids=evidence_ids,
                subject_type=subject_type or "Conversation",
                subject_id=subject_id or conversation_id,
                mode=mode,
                allow_diagnostic=allow_diagnostic,  # Communication optimization is not diagnostic
                allow_medical_action=False,         # This is process optimization, not a medical action
                require_evidence=require_evidence,
            )
            return result
        except Exception:
            # Any error in LLM processing - fail safe to None
            return None
            
    def get_supported_tasks(self) -> List[LLMTask]:
        """Return list of tasks this service can handle."""
        return [LLMTask.CONVERSATION_REPLY]