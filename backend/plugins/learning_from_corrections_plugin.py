"""
Learning from Corrections Plugin for Solenos.
Integrates the caregiving AI assistant's learning from corrections module into the Solenos backend.
"""

import sys
import os
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Add the medical_nlp directory to the path so we can import our module
MEDICAL_NLP_PATH = r"C:\medical_nlp"
if MEDICAL_NLP_PATH not in sys.path:
    sys.path.insert(0, MEDICAL_NLP_PATH)

try:
    from learning_from_corrections import LearningFromCorrections
    LEARNING_FROM_CORRECTIONS_AVAILABLE = True
except ImportError as e:
    LEARNING_FROM_CORRECTIONS_AVAILABLE = False
    print(f"Warning: Could not import LearningFromCorrections module: {e}")

# Initialize the learning from corrections module
if LEARNING_FROM_CORRECTIONS_AVAILABLE:
    learning_from_corrections = LearningFromCorrections()
    print("Learning from Corrections plugin initialized successfully")
else:
    learning_from_corrections = None
    print("Learning from Corrections plugin initialized in degraded mode")

# Create router
router = APIRouter(prefix="/learning-from-corrections", tags=["learning-from-corrections"])


# Pydantic models for request/response
class FeedbackRequest(BaseModel):
    feedback_text: str
    context: Dict[str, Any]
    feedback_type: str = "general"
    caregiver_id: Optional[str] = None
    is_verified: bool = False


class FeedbackResponse(BaseModel):
    feedback_id: str
    within_boundaries: bool
    boundary_details: Dict[str, Any]
    learning_extracted: Optional[Dict[str, Any]] = None
    feedback_count: int


class LearningSummaryResponse(BaseModel):
    total_feedback_received: int
    feedback_processed: int
    feedback_within_boundaries: int
    feedback_outside_boundaries: int
    learned_insights_count: int
    average_insight_confidence: float
    unique_patterns_learned: int
    top_learned_patterns: list
    learning_boundaries: Dict[str, Any]
    feedback_sources_tracked: int


class ApplicableLearningsResponse(BaseModel):
    communication_adjustments: list
    approach_suggestions: list
    confidence_level: str
    applicable_patterns: list


class ReliabilityUpdateRequest(BaseModel):
    caregiver_id: str
    reliability_score: float


def init_plugin(app):
    """Initialize the plugin with the FastAPI app."""
    app.include_router(router)
    print("Learning from Corrections plugin routes registered")


@router.post("/feedback", response_model=FeedbackResponse)
async def add_feedback(request: FeedbackRequest):
    """
    Add feedback from a caregiver for learning.
    """
    if not learning_from_corrections:
        raise HTTPException(status_code=503, detail="Learning from Corrections module not available")
    
    try:
        result = learning_from_corrections.add_feedback(
            feedback_text=request.feedback_text,
            context=request.context,
            feedback_type=request.feedback_type,
            caregiver_id=request.caregiver_id,
            is_verified=request.is_verified
        )
        return FeedbackResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing feedback: {str(e)}")


@router.get("/summary", response_model=LearningSummaryResponse)
async def get_learning_summary():
    """
    Get a summary of learning from corrections activity.
    """
    if not learning_from_corrections:
        raise HTTPException(status_code=503, detail="Learning from Corrections module not available")
    
    try:
        summary = learning_from_corrections.get_learning_summary()
        return LearningSummaryResponse(**summary)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting learning summary: {str(e)}")


@router.post("/applicable-learnings", response_model=ApplicableLearningsResponse)
async def get_applicable_learnings(context: Dict[str, Any]):
    """
    Get learnings that are applicable to a given context.
    """
    if not learning_from_corrections:
        raise HTTPException(status_code=503, detail="Learning from Corrections module not available")
    
    try:
        learnings = learning_from_corrections.get_applicable_learnings(context)
        return ApplicableLearningsResponse(**learnings)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting applicable learnings: {str(e)}")


@router.post("/update-reliability")
async def update_feedback_source_reliability(request: ReliabilityUpdateRequest):
    """
    Update the reliability score for a feedback source.
    """
    if not learning_from_corrections:
        raise HTTPException(status_code=503, detail="Learning from Corrections module not available")
    
    try:
        learning_from_corrections.update_feedback_source_reliability(
            request.caregiver_id,
            request.reliability_score
        )
        return {"status": "success", "message": f"Updated reliability for caregiver {request.caregiver_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating reliability: {str(e)}")


@router.get("/health")
async def health_check():
    """
    Health check endpoint for the plugin.
    """
    if learning_from_corrections:
        return {
            "status": "healthy",
            "plugin": "learning-from-corrections",
            "module_available": True
        }
    else:
        return {
            "status": "degraded",
            "plugin": "learning-from-corrections",
            "module_available": False,
            "message": "Learning from Corrections module not available"
        }