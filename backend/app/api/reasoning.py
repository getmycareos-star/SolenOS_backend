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
    person_id: str = Path(..., description="Person ID"),
    principal: AuthenticatedPrincipal = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Return a persisted, source-grounded daily view for an authorized care context.

    This endpoint reports recorded events, evidence, administered-dose records,
    typed vital observations, persisted follow-ups/questions/contradictions, and
    explicitly persisted care windows. It does not infer a medication schedule,
    predict a clinical event, or turn an unusual vital into a diagnosis.
    """
    if not principal.has_access_to_person(person_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this person",
        )

    from datetime import timedelta
    from app.models.care_event import CareEvent as CareEventModel
    from app.models.evidence import Evidence as EvidenceModel
    from app.models.daily_capture import MedicationRecord, VitalSign
    from app.models.follow_up import FollowUp
    from app.models.uncertainty import OpenQuestion, Contradiction
    from app.models.time_location import CareWindow

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    upcoming_until = now + timedelta(days=7)

    def enum_value(value):
        return getattr(value, "value", value)

    def as_iso(value):
        return value.isoformat() if value is not None else None

    def in_window(value, start, end):
        if value is None:
            return False
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return start <= value <= end

    recent_events = (
        db.query(CareEventModel)
        .filter(CareEventModel.person_id == person_id)
        .order_by(CareEventModel.occurred_at.desc().nullslast())
        .limit(100)
        .all()
    )
    recent_events = [
        event for event in recent_events
        if in_window(event.occurred_at, since, now)
    ][:20]

    recent_evidence = (
        db.query(EvidenceModel)
        .filter(EvidenceModel.person_id == person_id)
        .order_by(EvidenceModel.uploaded_at.desc().nullslast())
        .limit(100)
        .all()
    )
    recent_evidence = [
        item for item in recent_evidence
        if in_window(item.uploaded_at, since, now)
    ][:20]

    medication_records = (
        db.query(MedicationRecord)
        .filter(MedicationRecord.person_id == person_id)
        .order_by(MedicationRecord.recorded_at.desc().nullslast())
        .limit(100)
        .all()
    )
    medication_records = [
        item for item in medication_records
        if in_window(item.recorded_at, since, now)
    ][:20]

    vital_records = (
        db.query(VitalSign)
        .filter(VitalSign.person_id == person_id)
        .order_by(VitalSign.measured_at.desc())
        .limit(100)
        .all()
    )
    vital_records = [
        item for item in vital_records
        if in_window(item.measured_at, since, now)
    ][:20]

    follow_ups = (
        db.query(FollowUp)
        .filter(FollowUp.person_id == person_id, FollowUp.is_active.is_(True))
        .order_by(FollowUp.due_at.asc())
        .all()
    )
    overdue_items = []
    upcoming_items = []
    terminal_follow_up_statuses = {"completed", "cancelled", "canceled", "resolved", "closed"}
    for item in follow_ups:
        status_value = str(enum_value(item.status) or "").lower()
        if status_value in terminal_follow_up_statuses:
            continue
        due_at = item.due_at
        if due_at is None:
            continue
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        view = {
            "id": item.id,
            "what_to_recheck": item.what_to_recheck,
            "why_open": item.why_open,
            "due_at": due_at.isoformat(),
            "priority": enum_value(item.priority_hint),
            "evidence_ids": item.evidence_ids or [],
            "status": status_value or "unspecified",
        }
        if due_at <= now:
            overdue_items.append(view)
        elif due_at <= upcoming_until:
            upcoming_items.append(view)

    questions = (
        db.query(OpenQuestion)
        .filter(OpenQuestion.person_id == person_id)
        .order_by(OpenQuestion.asked_at.desc().nullslast())
        .limit(50)
        .all()
    )
    open_questions = [
        {
            "id": item.id,
            "question": item.question,
            "priority": enum_value(item.priority),
            "asked_at": as_iso(item.asked_at),
            "evidence_ids": item.evidence_ids or [],
        }
        for item in questions
        if str(item.status or "").lower() in {"open", "unresolved", "pending"}
    ]

    contradiction_rows = (
        db.query(Contradiction)
        .filter(Contradiction.person_id == person_id)
        .order_by(Contradiction.created_at.desc().nullslast())
        .limit(50)
        .all()
    )
    unresolved_contradictions = [
        {
            "id": item.id,
            "field": item.field,
            "description": item.description,
            "evidence_ids": item.evidence_ids or [],
            "created_at": as_iso(item.created_at),
        }
        for item in contradiction_rows
        if str(item.resolution_status or "").lower() not in {"resolved", "closed", "accepted"}
    ]

    windows = (
        db.query(CareWindow)
        .filter(CareWindow.person_id == person_id)
        .order_by(CareWindow.started_at.desc())
        .limit(100)
        .all()
    )
    active_windows = []
    expired_windows = []
    for item in windows:
        status_value = str(item.status or "").lower()
        end_at = item.ends_at
        if end_at is not None and end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=timezone.utc)
        view = {
            "id": item.id,
            "window_type": item.window_type,
            "started_at": as_iso(item.started_at),
            "ends_at": as_iso(item.ends_at),
            "status": status_value,
            "time_provenance": item.time_provenance,
            "source_event_id": item.source_event_id,
        }
        if status_value == "active" and end_at is not None and end_at >= now:
            active_windows.append(view)
        elif status_value == "active" and end_at is not None and end_at < now:
            expired_windows.append(view)

    recent_activity = {
        "events": [
            {
                "id": item.id,
                "event_type": enum_value(item.event_type),
                "title": item.title,
                "description": item.description,
                "occurred_at": as_iso(item.occurred_at),
                "evidence_ids": item.evidence_ids or [],
            }
            for item in recent_events
        ],
        "evidence": [
            {
                "id": item.id,
                "type": enum_value(item.type),
                "source_text": item.source_text,
                "uploaded_at": as_iso(item.uploaded_at),
                "evidence_status": enum_value(getattr(item, "evidence_status", None)),
            }
            for item in recent_evidence
        ],
        "medication_records": [
            {
                "id": item.id,
                "medication_name": item.medication_name,
                "status": enum_value(item.status),
                "scheduled_for": as_iso(item.scheduled_for),
                "administered_at": as_iso(item.administered_at),
                "recorded_at": as_iso(item.recorded_at),
                "note": item.note,
                "evidence_id": item.evidence_id,
                "care_event_id": item.care_event_id,
            }
            for item in medication_records
        ],
        "vitals": [
            {
                "id": item.id,
                "vital_type": enum_value(item.vital_type),
                "value": item.value,
                "unit": item.unit,
                "measured_at": as_iso(item.measured_at),
                "is_out_of_range": item.is_out_of_range,
                "range_note": item.range_note,
                "evidence_id": item.evidence_id,
                "care_event_id": item.care_event_id,
            }
            for item in vital_records
        ],
    }

    activity_count = sum(len(values) for values in recent_activity.values())
    summary_parts = [
        f"{len(recent_events)} recorded event(s)",
        f"{len(recent_evidence)} evidence item(s)",
        f"{len(medication_records)} medication administration record(s)",
        f"{len(vital_records)} vital observation(s)",
        f"{len(overdue_items)} overdue follow-up(s)",
        f"{len(open_questions)} open question(s)",
        f"{len(unresolved_contradictions)} unresolved contradiction(s)",
    ]
    daily_summary = "Last 24 hours: " + ", ".join(summary_parts) + "."
    if activity_count == 0:
        daily_summary += " No event, evidence, medication, or vital record was found in this window."
    if overdue_items:
        daily_summary += " One or more persisted follow-ups are overdue and need caregiver review."
    if unresolved_contradictions:
        daily_summary += " Conflicting information remains unresolved; this summary does not choose a side."
    if open_questions:
        daily_summary += " Open questions remain unanswered in the record."
    if not active_windows and not expired_windows:
        daily_summary += " No persisted care window is active or expired in the current record."
    daily_summary += " Only recorded follow-ups and care windows are listed; no schedule or clinical conclusion was inferred."

    return {
        "id": str(uuid.uuid4()),
        "person_id": person_id,
        "intelligence_date": now.isoformat(),
        "window_start": since.isoformat(),
        "window_end": now.isoformat(),
        "recent_activity": recent_activity,
        "overdue_items": overdue_items,
        "upcoming_items": upcoming_items,
        "open_questions": open_questions,
        "unresolved_contradictions": unresolved_contradictions,
        "active_windows": active_windows,
        "expired_windows": expired_windows,
        "daily_summary": daily_summary,
    }
