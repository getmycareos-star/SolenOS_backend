from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from datetime import datetime

from app.core.database import get_db
from app.schemas.analyze import (
    AnalyzeRequest, AnalyzeResponse,
    RiskAssessmentRequest, RiskAssessmentResponse,
    UncertaintyAssessmentRequest, UncertaintyAssessmentResponse,
    LoadAssessmentRequest, LoadAssessmentResponse,
    MeetingPrepRequest, MeetingPrepResult,
    MemoryReconstructionRequest, MemoryReconstructionResult,
    CareJourneyRequest, CareJourneyGraphResult,
)
from app.services.analyze import AnalyzeService

router = APIRouter(prefix="/analyze", tags=["analyze"])


@router.post("", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest, db: Session = Depends(get_db)):
    """Main deep analysis endpoint - exposes the full engine pipeline.
    
    This is the caregiver-facing deep reasoning endpoint that runs the complete
    backend-fix engine stack: trust layer, crisis prevention, situation risk,
    missing information queue, caregiver load, responsibility graph, delegation,
    epistemic safety, meeting prep, cognitive relief, care journey graph,
    and memory reconstruction.
    """
    service = AnalyzeService(db)
    result = service.analyze(request.model_dump())
    return result


@router.post("/risk", response_model=RiskAssessmentResponse)
def assess_risk(request: RiskAssessmentRequest, db: Session = Depends(get_db)):
    """Assess risk level and crisis triggers for a situation."""
    service = AnalyzeService(db)
    return service.assess_risk(request.person_id, request.situation_text, request.context or {})


@router.post("/uncertainty", response_model=UncertaintyAssessmentResponse)
def assess_uncertainty(request: UncertaintyAssessmentRequest, db: Session = Depends(get_db)):
    """Assess uncertainty, gaps, and trust for a set of claims."""
    service = AnalyzeService(db)
    return service.assess_uncertainty(request.person_id, request.claims, request.context or {})


@router.post("/load", response_model=LoadAssessmentResponse)
def assess_load(request: LoadAssessmentRequest, db: Session = Depends(get_db)):
    """Assess caregiver load, responsibility graph, and delegation."""
    service = AnalyzeService(db)
    return service.assess_load(request.caregiver_id, request.person_id, request.time_window_hours)


@router.post("/meeting-prep", response_model=MeetingPrepResult)
def prepare_meeting(request: MeetingPrepRequest, db: Session = Depends(get_db)):
    """Generate meeting preparation materials."""
    service = AnalyzeService(db)
    return service.prepare_meeting(request.person_id, request.meeting_type, request.scheduled_at)


@router.post("/memory", response_model=MemoryReconstructionResult)
def reconstruct_memory(request: MemoryReconstructionRequest, db: Session = Depends(get_db)):
    """Reconstruct memories for a query."""
    service = AnalyzeService(db)
    return service.reconstruct_memory(request.person_id, request.query, request.time_range)


@router.post("/journey", response_model=CareJourneyGraphResult)
def get_care_journey(request: CareJourneyRequest, db: Session = Depends(get_db)):
    """Get care journey graph for a person."""
    service = AnalyzeService(db)
    return service.get_care_journey(request.person_id, request.start_date, request.end_date)


@router.get("/engines")
def list_engines():
    """List all available deep analysis engines."""
    return {
        "engines": [
            {"name": "trust_layer", "description": "Known/assumed/unknown separation with confidence"},
            {"name": "crisis_prevention", "description": "Risk level detection and recommended actions"},
            {"name": "situation_risk_register", "description": "Situation risk scoring and critical count"},
            {"name": "missing_information_queue", "description": "Information gaps and priority questions"},
            {"name": "caregiver_load", "description": "Caregiver burden scoring and depletion signals"},
            {"name": "responsibility_graph", "description": "Responsibilities, gaps, and conflicts"},
            {"name": "delegation", "description": "Delegation tracking and pending acceptance"},
            {"name": "epistemic_safety", "description": "Safety violations and medical boundary checks"},
            {"name": "meeting_prep", "description": "Agenda, questions, and documents for appointments"},
            {"name": "cognitive_relief", "description": "Load reduction and priority simplification"},
            {"name": "care_journey_graph", "description": "Care timeline nodes, edges, and events"},
            {"name": "memory_reconstruction", "description": "Memory reconstruction with confidence"},
        ],
        "note": "All engines run in the main /analyze pipeline. Individual endpoints available for targeted use."
    }