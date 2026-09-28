from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.core.database import get_db
from app.models.llm import LLMCall
from app.schemas.care_context import (
    LLMCallResponse,
    LLMHealthResponse,
    LLMReasonRequest,
    LLMReasonResponse,
    LLMReasoningResultResponse,
)
from app.services.llm import LocalReasoningEngine

router = APIRouter(prefix="/llm", tags=["llm"])


def get_engine(db: Session = Depends(get_db)) -> LocalReasoningEngine:
    return LocalReasoningEngine(db)


@router.get("/health", response_model=LLMHealthResponse)
def llm_health(engine: LocalReasoningEngine = Depends(get_engine)):
    return engine.health()


@router.post("/reset-circuit")
def reset_circuit():
    LocalReasoningEngine.reset_circuit()
    return {"ok": True, "message": "Local model circuit reset."}


@router.get("/calls", response_model=List[LLMCallResponse])
def list_calls(
    person_id: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """The audit trail of every attempt to reach the local model.

    Kept visible on purpose. A caregiver correcting a bad model call needs to
    be able to find it, and a reviewer needs to be able to see what was
    dropped and why.
    """
    query = db.query(LLMCall)
    if person_id:
        query = query.filter(LLMCall.person_id == person_id)
    return query.order_by(LLMCall.called_at.desc()).limit(limit).all()


@router.get("/calls/{call_id}", response_model=LLMCallResponse)
def get_call(call_id: str, db: Session = Depends(get_db)):
    call = db.query(LLMCall).filter(LLMCall.id == call_id).first()
    if call is None:
        raise HTTPException(status_code=404, detail="LLM call not found")
    return call


@router.post("/reason", response_model=LLMReasonResponse)
def reason(payload: LLMReasonRequest, engine: LocalReasoningEngine = Depends(get_engine)):
    """Run one guarded reasoning step.

    Returns ok=true with a null result when nothing survived the guard or the
    model was unavailable. That is not an error: the caller falls back to the
    deterministic answer, which is the designed behaviour rather than a failure.
    """
    result = engine.reason(
        task=payload.task,
        person_id=payload.person_id,
        payload=payload.payload,
        evidence_ids=payload.evidence_ids,
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
                "No local reasoning result was produced. The deterministic answer stands. "
                "Check /api/v1/llm/health for the reason."
            ),
        )
    return LLMReasonResponse(
        ok=True,
        result=LLMReasoningResultResponse.model_validate(result),
        message="Local reasoning result produced and trust-checked.",
    )
