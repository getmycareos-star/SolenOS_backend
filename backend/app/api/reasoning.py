"""Reasoning API - RESTful endpoints for deep reasoning services."""
from fastapi import APIRouter, Depends, HTTPException, Path, Body, status
import uuid
from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from app.services.learning import LearningEngine
from app.services.dementia_reasoning import DementiaReasoningEngine
from app.services.longitudinal_evidence_service import LongitudinalEvidenceService
from app.services.care_continuity import CareContinuityEngine
from app.services.causal_inference import CausalInferenceService
from app.services.belief_updater import BeliefUpdaterService
from app.services.explainability import ExplainabilityService
from app.models.care_recommendations import CareRecommendation
from app.schemas.memory import ReasoningMemory, LearningEvent
from app.schemas.dementia import DementiaObservationResponse, DementiaReasoningSummary
from app.schemas.longitudinal_evidence import LongitudinalEvidenceEvent, LongitudinalEvidenceTrend
from app.schemas.care_context import ContinuityInsightResponse

from app.core.database import get_db
from app.core.security import get_current_active_user, AuthenticatedPrincipal
from app.core.cache_decorators import cached_response

router = APIRouter(tags=["reasoning"])


def get_causal_inference_service(db: Session = Depends(get_db)) -> CausalInferenceService:
    """Dependency injection factory for causal inference service."""
    return CausalInferenceService(db)


def get_belief_updater_service(db: Session = Depends(get_db)) -> BeliefUpdaterService:
    """Dependency injection factory for belief updater service."""
    return BeliefUpdaterService(db)


def get_explainability_service(db: Session = Depends(get_db)) -> ExplainabilityService:
    """Dependency injection factory for explainability service."""
    return ExplainabilityService(db)


def get_learning_service(db: Session = Depends(get_db)) -> LearningEngine:
    """Dependency injection factory for learning service."""
    return LearningEngine(db)


def get_dementia_service(db: Session = Depends(get_db)) -> DementiaReasoningEngine:
    """Dependency injection factory for dementia reasoning service."""
    return DementiaReasoningEngine(db)


def get_longitudinal_evidence_service(db: Session = Depends(get_db)) -> LongitudinalEvidenceService:
    """Dependency injection factory for longitudinal evidence service."""
    return LongitudinalEvidenceService(db)


def get_care_continuity_service(db: Session = Depends(get_db)) -> CareContinuityEngine:
    """Dependency injection factory for care continuity service."""
    return CareContinuityEngine(db)


@router.post(
    "/graph/{person_id}/counterfactual",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def run_counterfactual(
    person_id: uuid.UUID = Path(..., description="Person uuid.UUID"),
    payload: Dict[str, Any] = Body(..., description="Intervention specification"),
    service: CausalInferenceService = Depends(get_causal_inference_service),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
):
    """Run a counterfactual simulation on the Care Graph.

    Accept both the wrapped client shape {"intervention": {...}} and the raw
    intervention object. The normalized intervention is echoed in the response.
    """
    if not principal.has_access_to_person(str(person_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )
    intervention = payload.get("intervention") if isinstance(payload.get("intervention"), dict) else payload
    return service.counterfactual_simulation(str(person_id), intervention)


@router.post(
    "/belief/{person_id}/update",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def update_belief(
    person_id: uuid.UUID = Path(..., description="Person uuid.UUID"),
    service: BeliefUpdaterService = Depends(get_belief_updater_service),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
):
    """Update belief state for a person based on their evidence."""
    if not principal.has_access_to_person(str(person_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )
    belief_state = service.update_belief(str(person_id))
    return {
        "belief_state_id": belief_state.id,
        "person_id": belief_state.person_id,
        "state_variables": belief_state.state_variables,
        "updated_at": belief_state.updated_at.isoformat(),
    }


@cached_response(ttl=300, key_prefix="reasoning_explain")
@router.get(
    "/explain/{decision_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def explain_decision(
    decision_id: uuid.UUID = Path(..., description="Decision uuid.UUID"),
    service: ExplainabilityService = Depends(get_explainability_service),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
):
    """Get an explanation for a decision."""
    # Resolve the owning person before returning provenance. A decision ID is
    # not an authorization credential, and provenance may expose sensitive care
    # evidence from multiple sources.
    decision = service.db.query(CareRecommendation).filter(
        CareRecommendation.id == str(decision_id)
    ).first()
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Decision not found",
        )
    if not principal.has_access_to_person(str(decision.person_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this decision",
        )
    return service.get_provenance_chain(str(decision_id))


@cached_response(ttl=300, key_prefix="reasoning_summary")
@router.get(
    "/{person_id}/summary",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def get_reasoning_summary_endpoint(
    person_id: str = Path(..., description="Person uuid.UUID"),
    service: LearningEngine = Depends(get_learning_service),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
):
    """Get the reasoning summary for a person."""
    if not principal.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )
    raw_summary = service.get_reasoning_summary(person_id)
    
    # Transform the data to match the frontend's ReasoningSummary interface
    # We keep the confidence field for all memory types, not just confirmed_facts
    transformed = {}
    
    # For all memory types, keep key, value, and confidence
    for mem_type in ["confirmed_facts", "open_questions", "preferences", "rejected_assumptions", "care_patterns", "coordination_decisions"]:
        if mem_type in raw_summary:
            transformed[mem_type] = [
                {
                    "key": item["key"],
                    "value": item["value"],
                    "confidence": item["confidence"]
                }
                for item in raw_summary[mem_type]
            ]
    
    return transformed


@cached_response(ttl=300, key_prefix="reasoning_memory")
@router.get(
    "/reasoning-memory/{person_id}",
    response_model=List[ReasoningMemory],
    status_code=status.HTTP_200_OK,
)
def get_reasoning_memory(
    person_id: str = Path(..., description="Person uuid.UUID"),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Get reasoning memories for a person."""
    if not principal.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )
    from app.models.memory import ReasoningMemory as ReasoningMemoryModel
    memories = db.query(ReasoningMemoryModel).filter(ReasoningMemoryModel.person_id == person_id).all()
    return memories


@cached_response(ttl=300, key_prefix="learning_events")
@router.get(
    "/learning-events/{person_id}",
    response_model=List[LearningEvent],
    status_code=status.HTTP_200_OK,
)
def get_learning_events(
    person_id: str = Path(..., description="Person uuid.UUID"),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Get learning events for a person."""
    if not principal.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )
    from app.models.memory import LearningEvent as LearningEventModel
    events = db.query(LearningEventModel).filter(LearningEventModel.person_id == person_id).all()
    return events


@cached_response(ttl=300, key_prefix="observations")
@router.get(
    "/observations/{person_id}",
    response_model=List[DementiaObservationResponse],
    status_code=status.HTTP_200_OK,
)
def get_observations(
    person_id: str = Path(..., description="Person uuid.UUID"),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Get observations for a person."""
    if not principal.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )
    service = get_dementia_service(db)
    # Assuming the dementia service has a method to get observations
    # If not, we can query the model directly
    from app.models.dementia import DementiaObservation as DementiaObservationModel
    observations = db.query(DementiaObservationModel).filter(DementiaObservationModel.person_id == person_id).all()
    return observations


@cached_response(ttl=300, key_prefix="events")
@router.get(
    "/events/{person_id}",
    response_model=List[LongitudinalEvidenceEvent],
    status_code=status.HTTP_200_OK,
)
def get_events(
    person_id: str = Path(..., description="Person uuid.UUID"),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Get events for a person."""
    if not principal.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )
    service = get_longitudinal_evidence_service(db)
    events = service.get_events(person_id)
    return events


@cached_response(ttl=300, key_prefix="insights")
@router.get(
    "/insights/{person_id}",
    response_model=List[ContinuityInsightResponse],
    status_code=status.HTTP_200_OK,
)
def get_insights(
    person_id: str = Path(..., description="Person uuid.UUID"),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Get insights for a person."""
    if not principal.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )
    service = get_care_continuity_service(db)
    # Get the continuity brief data
    brief_data = service.continuity_brief(person_id)
    
    # Generate insights from the brief data
    insights = []
    
    # Insight about days since last record
    days_since_last = brief_data.get("days_since_last_record")
    if days_since_last is not None and days_since_last > 0:
        insights.append(ContinuityInsightResponse(
            id=f"insights-days-since-last-{person_id}-{days_since_last}",
            title=f"No records for {days_since_last} day{'s' if days_since_last != 1 else ''}",
            description=f"It has been {days_since_last} day{'s' if days_since_last != 1 else ''} since the last recorded event for this person."
        ))
    
    # Insight about longest gap
    longest_gap = brief_data.get("longest_gap_days")
    if longest_gap is not None and longest_gap > 0:
        insights.append(ContinuityInsightResponse(
            id=f"insights-longest-gap-{person_id}-{longest_gap}",
            title=f"Longest gap: {longest_gap} day{'s' if longest_gap != 1 else ''}",
            description=f"The longest period without any records is {longest_gap} day{'s' if longest_gap != 1 else ''}."
        ))
    
    # Insight about caregiver changes
    caregiver_changes = brief_data.get("caregiver_changes", 0)
    if caregiver_changes > 0:
        insights.append(ContinuityInsightResponse(
            id=f"insights-caregiver-changes-{person_id}-{caregiver_changes}",
            title=f"{caregiver_changes} caregiver change{'s' if caregiver_changes != 1 else ''}",
            description=f"There have been {caregiver_changes} caregiver change{'s' if caregiver_changes != 1 else ''} in the record."
        ))
    
    # Insight about setting changes
    setting_changes = brief_data.get("setting_changes", 0)
    if setting_changes > 0:
        insights.append(ContinuityInsightResponse(
            id=f"insights-setting-changes-{person_id}-{setting_changes}",
            title=f"{setting_changes} setting change{'s' if setting_changes != 1 else ''}",
            description=f"There have been {setting_changes} setting change{'s' if setting_changes != 1 else ''} in the record."
        ))
    
    # Insights about stale threads
    stale_threads = brief_data.get("stale_threads", [])
    for i, thread_label in enumerate(stale_threads):
        # We would need to get the quiet_for_days for each thread, but we don't have that easily
        # from the brief data. Let's skip this for now or use a placeholder.
        insights.append(ContinuityInsightResponse(
            id=f"insights-stale-thread-{person_id}-{i}",
            title=f"Thread '{thread_label}' has been quiet",
            description=f"The thread '{thread_label}' has not had any recent recordings."
        ))
    
    # Insight about the one line that matters
    one_line = brief_data.get("the_one_line_that_matters")
    if one_line:
        insights.append(ContinuityInsightResponse(
            id=f"insights-one-line-{person_id}-{hash(one_line) % 10000}",
            title="Key insight",
            description=one_line
        ))
    
    # If we didn't generate any insights, add a default one
    if not insights:
        insights.append(ContinuityInsightResponse(
            id=f"insights-default-{person_id}-{int(datetime.now(timezone.utc).timestamp())}",
            title="No significant insights",
            description="No significant patterns or changes were detected in the available data."
        ))
    
    return insights


@cached_response(ttl=300, key_prefix="observation_trends")
@router.get(
    "/observations/{person_id}/trends",
    response_model=List[LongitudinalEvidenceTrend],
    status_code=status.HTTP_200_OK,
)
def get_observation_trends(
    person_id: str = Path(..., description="Person uuid.UUID"),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Get observation trends for a person."""
    if not principal.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )
    service = get_longitudinal_evidence_service(db)
    trends = service.get_trends(person_id)
    return trends


@cached_response(ttl=300, key_prefix="reasoning_person_summary")
@router.get(
    "/reasoning/{person_id}/summary",
    response_model=DementiaReasoningSummary,
    status_code=status.HTTP_200_OK,
)
def get_reasoning_summary(
    person_id: str = Path(..., description="Person uuid.UUID"),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Get the reasoning summary for a person."""
    if not principal.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )
    service = get_dementia_service(db)
    summary = service.get_summary(person_id)
    return summary


@cached_response(ttl=300, key_prefix="daily_intelligence")
@router.get(
    "/daily-intelligence/{person_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
)
def get_daily_intelligence(
    person_id: str = Path(..., description="Person uuid.UUID"),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Get daily intelligence for a person."""
    if not principal.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )
    # TODO: Implement daily intelligence logic
    # For now, return a basic structure
    # In a real implementation, this would generate actual daily intelligence
    # based on recent data, patterns, and care context
    try:
        # Attempt to get daily intelligence from services
        # This would integrate with various services to generate real intelligence
        
        # Get recent events and observations
        from app.models.care_event import CareEvent as CareEventModel
        from app.models.evidence import Evidence as EvidenceModel
        from app.models.medication import Medication as MedicationRecord
        from datetime import timedelta
        
        # Get data from the last 24 hours
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        
        recent_events = (
            db.query(CareEventModel)
            .filter(
                CareEventModel.person_id == person_id,
                CareEventModel.occurred_at >= yesterday
            )
            .order_by(CareEventModel.occurred_at.desc())
            .limit(10)
            .all()
        )
        
        recent_evidence = (
            db.query(EvidenceModel)
            .filter(
                EvidenceModel.person_id == person_id,
                EvidenceModel.uploaded_at >= yesterday
            )
            .order_by(EvidenceModel.uploaded_at.desc())
            .limit(10)
            .all()
        )
        
        recent_medications = (
            db.query(MedicationRecord)
            .filter(
                MedicationRecord.person_id == person_id,
                MedicationRecord.recorded_at >= yesterday
            )
            .order_by(MedicationRecord.recorded_at.desc())
            .limit(10)
            .all()
        )
        
        # Build intelligence based on actual data
        intelligence_date = datetime.now(timezone.utc)
        
        # Extract overdue items (simplified)
        overdue_items = []
        # In reality, this would check scheduled items that are past due
        
        # Extract upcoming items (simplified)
        upcoming_items = []
        # In reality, this would check scheduled items in the near future
        
        # Extract active windows (simplified)
        active_windows = []
        # In reality, this would check time-sensitive care windows
        
        # Extract expired windows (simplified)
        expired_windows = []
        # In reality, this would check expired time-sensitive windows
        
        # Generate daily summary based on actual data
        if recent_events or recent_evidence or recent_medications:
            daily_summary = f"Daily intelligence for {person_id} based on {len(recent_events)} recent events, {len(recent_evidence)} recent observations, and {len(recent_medications)} recent medication records."
        else:
            daily_summary = f"No significant activity recorded in the last 24 hours for person {person_id}."
        
        return {
            "id": str(uuid.uuid4()),
            "intelligence_date": intelligence_date.isoformat(),
            "overdue_items": overdue_items,
            "upcoming_items": upcoming_items,
            "active_windows": active_windows,
            "expired_windows": expired_windows,
            "daily_summary": daily_summary
        }
    except Exception as e:
        # Fallback to basic structure if there's an error
        return {
            "id": str(uuid.uuid4()),
            "intelligence_date": datetime.now(timezone.utc).isoformat(),
            "overdue_items": [],
            "upcoming_items": [],
            "active_windows": [],
            "expired_windows": [],
            "daily_summary": f"Daily intelligence for {person_id} - basic implementation (error retrieving detailed data: {str(e)})"
        }