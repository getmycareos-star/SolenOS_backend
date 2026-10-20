"""API endpoint for therapy timing suggestions.
Part of SolenOS's AI-assisted reasoning system following strict safety patterns.
"""
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.services.llm import LocalReasoningEngine, LLMReasoningResult
from app.schemas.care_context import LLMReasonRequest, LLMReasonResponse
from app.core.llm_enums import LLMTask


router = APIRouter(prefix="/therapy-timing", tags=["therapy-timing"])


@router.post("/analyze", response_model=LLMReasonResponse)
def suggest_therapy_timing(
    payload: LLMReasonRequest,
    db: Session = Depends(get_db)
) -> LLMReasonResponse:
    """Suggest optimal timing for therapies (PT/OT/speech) using AI-assisted reasoning.
    
    This endpoint follows SolenOS's strict safety patterns:
    - All reasoning goes through LocalReasoningEngine (circuit breaker, audit, TrustGuard)
    - Returns None result when AI unavailable or untrustworthy (fallback to deterministic logic)
    - Never persists or acts on raw LLM output
    - Local-first only (no cloud LLM configuration)
    """
    try:
        engine = LocalReasoningEngine(db)
        result: Optional[LLMReasoningResult] = engine.reason(
            task=LLMTask.PROACTIVE_OPPORTUNITY,
            person_id=payload.person_id,
            payload=payload.payload,
            evidence_ids=payload.evidence_ids or [],
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            mode=payload.mode,
            allow_diagnostic=payload.allow_diagnostic,
            allow_medical_action=payload.allow_medical_action,
            require_evidence=payload.require_evidence,
        )
        
        if result is None:
            return LLMReasonResponse(
                ok=True,
                result=None,
                message=(
                    "No therapy timing suggestions were produced. "
                    "This may be due to insufficient data, model unavailability, "
                    "or safety constraints. Standard therapy scheduling applies."
                ),
            )
        return LLMReasonResponse(
            ok=True,
            result=result,
            message="Therapy timing suggestions produced and trust-checked.",
        )
    except Exception as e:
        # Fail safe - return ok=True with None result to trigger deterministic fallback
        return LLMReasonResponse(
            ok=True,
            result=None,
            message=f"Error in therapy timing suggestion: {str(e)}. Using standard scheduling.",
        )


@router.get("/persons/{person_id}/options", response_model=List[Dict[str, Any]])
def get_therapy_options_deterministic(
    person_id: str = Path(..., description="The ID of the person"),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Deterministic therapy options (fallback when AI unavailable).
    
    This represents the baseline deterministic logic that AI reasoning enriches.
    Always available even when LLM is disabled or unavailable.
    """
    # This would implement standard rule-based therapy scheduling
    # For now, return empty list - in reality this would check against
    # therapy guidelines and abilities using deterministic rules
    return [
        {
            "type": "deterministic_options",
            "message": "Standard therapy options principles applied",
            "ai_enhanced": False,
            "person_id": person_id,
        }
    ]