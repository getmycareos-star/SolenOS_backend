"""API endpoints for the Care Event Engine.

Endpoints:
  - POST /api/care-events/detect          - Detect candidate events from evidence
  - POST /api/care-events/classify          - Classify detection candidates
  - POST /api/care-events                    - Create a new care event
  - GET  /api/care-events                    - Search/query care events
  - GET  /api/care-events/{id}              - Get a single event
  - PATCH /api/care-events/{id}             - Update an event
  - GET  /api/care-events/{id}/trace         - Full traceability chain
  - GET  /api/care-events/{id}/graph         - Event relationship graph
  - DELETE /api/care-events/{id}             - Invalidate an event
  - POST /api/care-events/{id}/correct       - Correct an event
  - POST /api/care-events/{id}/supersede     - Supersede an event
  - POST /api/care-events/relationships      - Create event relationship
  - GET  /api/care-events/relationships/{id} - Get relationship
  - POST /api/care-events/groups             - Create event group
  - GET  /api/care-events/groups/{person_id} - Get event groups
  - POST /api/care-events/recurrence        - Detect recurrence patterns
  - POST /api/care-events/state-impacts     - Infer/validate state impacts
  - GET  /api/care-events/{id}/state-impacts - Get state impacts for event
  - POST /api/care-events/dedup/propose     - Propose mention deduplication
  - POST /api/care-events/dedup/resolve      - Resolve deduplication
  - POST /api/care-events/historical-knowledge - Query historical knowledge state
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.event_enums import (
    CareEventType,
    CareEventSubtype,
    CareEventStatus,
    CareEventSeverity,
    CareEventRelationshipType,
    CareEventGroupingType,
    EventStateImpactType,
    EventStateImpactConfidence,
    EventMentionDeduplicationStatus,
    EventInvalidationReason,
    EventSupersessionReason,
    EventLifecycleStage,
    EventMentionType,
    RecurrenceDetectionMethod,
)
from app.models.care_event import (
    CareEvent,
    CareEventVersion,
    CareEventRelationship,
    CareEventRecurrenceGroup,
    CareEventGroup,
    CareEventCorrection,
    CareEventInvalidation,
    CareEventSupersession,
    CareEventMentionDeduplication,
    CareEventStateImpact,
)
from app.models.evidence import Evidence
from app.schemas.care_event import (
    CareEventCreate,
    CareEventUpdate,
    CareEventResponse,
    CareEventQueryParams,
    CareEventTraceResponse,
    CareEventGraphResponse,
    CareEventRelationshipCreate,
    CareEventRelationshipResponse,
    CareEventRecurrenceGroupCreate,
    CareEventRecurrenceGroupResponse,
    CareEventGroupCreate,
    CareEventGroupResponse,
    CareEventCorrectionCreate,
    CareEventCorrectionResponse,
    CareEventInvalidationCreate,
    CareEventInvalidationResponse,
    CareEventSupersessionCreate,
    CareEventSupersessionResponse,
    CareEventMentionDeduplicationCreate,
    CareEventMentionDeduplicationResponse,
    CareEventStateImpactCreate,
    CareEventStateImpactResponse,
    CareEventVersionResponse,
    HistoricalKnowledgeQuery,
    HistoricalKnowledgeResponse,
)
from app.services.event_detection import EventDetectionEngine, EventDetectionResult
from app.services.event_classification import EventClassificationService
from app.services.event_dedup import EventDeduplicationService
from app.services.event_lifecycle import EventLifecycleService
from app.services.event_relationships import EventRelationshipService
from app.services.event_recurrence import EventRecurrenceService
from app.services.event_state_impact import EventStateImpactService

router = APIRouter(tags=["care-events"])

detection_engine = EventDetectionEngine()
classification_service = EventClassificationService()
dedup_service = EventDeduplicationService()
lifecycle_service = EventLifecycleService()
relationship_service = EventRelationshipService()
recurrence_service = EventRecurrenceService()
state_impact_service = EventStateImpactService

# Cache imports
from app.core.cache import cache_service
from app.core.cache_key_utils import CacheKeyPrefix, CacheTTL


@router.post("/care-events/detect", response_model=List[Dict[str, Any]])
def detect_events(
    person_id: str = Query(..., description="Person ID to detect events for"),
    evidence_ids: Optional[List[str]] = Query(default=None, description="Filter by specific evidence IDs"),
    event_types: Optional[List[CareEventType]] = Query(default=None, description="Filter by event types"),
    db: Session = Depends(get_db),
):
    """Detect candidate care events from evidence items."""
    candidates = detection_engine.detect_events_from_evidence(
        db=db,
        evidence_ids=evidence_ids,
        person_id=person_id,
    )

    results = []
    for candidate in candidates:
        if event_types and candidate.event_type not in event_types:
            continue
        results.append({
            "event_type": candidate.event_type.value if hasattr(candidate.event_type, 'value') else str(candidate.event_type),
            "event_subtype": candidate.event_subtype.value if candidate.event_subtype and hasattr(candidate.event_subtype, 'value') else (candidate.event_subtype or None),
            "title": candidate.title,
            "original_expression": candidate.original_expression,
            "person_id": candidate.person_id,
            "evidence_ids": candidate.evidence_ids,
            "confidence": candidate.confidence.to_dict(),
            "mention_type": candidate.mention_type.value if hasattr(candidate.mention_type, 'value') else str(candidate.mention_type),
            "validation_status": candidate.validation_status,
            "quality_gate_passed": candidate.quality_gate_passed,
            "temporal": {
                "event_time": candidate.event_time.isoformat() if candidate.event_time else None,
                "event_time_granularity": str(candidate.event_time_granularity) if candidate.event_time_granularity else None,
                "knowledge_time": candidate.knowledge_time.isoformat() if candidate.knowledge_time else None,
            },
        })

    return results


@router.post("/care-events/classify", response_model=List[Dict[str, Any]])
def classify_candidates(
    person_id: str = Query(..., description="Person ID"),
    evidence_ids: Optional[List[str]] = Query(default=None, description="Specific evidence IDs"),
    db: Session = Depends(get_db),
):
    """Detect and classify candidate care events."""
    candidates = detection_engine.detect_events_from_evidence(
        db=db,
        evidence_ids=evidence_ids,
        person_id=person_id,
    )

    classifications = classification_service.classify_candidates(candidates)
    results = []

    for classification in classifications:
        results.append({
            "event_type": classification.classified_event_type.value if classification.classified_event_type else None,
            "event_subtype": classification.classified_subtype.value if classification.classified_subtype else None,
            "title": classification.candidate.title,
            "status": classification.classified_status.value,
            "severity": classification.classified_severity.value,
            "mention_type": classification.classified_mention_type.value,
            "confidence": classification.candidate.confidence.to_dict(),
            "original_expression": classification.candidate.original_expression,
            "state_impacts": classification.state_impacts,
            "outcome": {
                "outcome": classification.outcome,
                "outcome_type": classification.outcome_type,
                "outcome_confidence": classification.outcome_confidence,
            },
            "is_valid": classification.is_valid,
            "validation_status": classification.candidate.validation_status,
        })

    return results


@router.post("/care-events", response_model=CareEventResponse)
def create_care_event(
    event: CareEventCreate,
    db: Session = Depends(get_db),
):
    """Create a new care event."""
    # Validate person exists
    from app.models.identity import Person
    person = db.get(Person, event.person_id)
    if not person:
        raise HTTPException(status_code=404, detail=f"Person {event.person_id} not found")

    # Validate primary evidence if provided
    if event.primary_evidence_id:
        evidence = db.get(Evidence, event.primary_evidence_id)
        if not evidence:
            raise HTTPException(status_code=404, detail=f"Evidence {event.primary_evidence_id} not found")
        if evidence.subject_id != event.person_id:
            raise HTTPException(
                status_code=400,
                detail=f"Person contamination: evidence subject_id={evidence.subject_id} != event person_id={event.person_id}",
            )

    # Validate all evidence links
    if event.evidence_ids:
        for eid in event.evidence_ids:
            ev = db.get(Evidence, eid)
            if not ev:
                raise HTTPException(status_code=404, detail=f"Evidence {eid} not found")
            if ev.subject_id != event.person_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Person contamination: evidence {eid} belongs to person {ev.subject_id}, event to {event.person_id}",
                )

    # Check for duplicates before creating
    existing = db.query(CareEvent).filter(
        CareEvent.person_id == event.person_id,
        CareEvent.event_type == event.event_type,
        CareEvent.event_time == event.event_time,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Event already exists (id={existing.id}). Use deduplication or supersession endpoints.",
        )

    # Create the event
    event_data = event.model_dump()
    # Map confidence dimensions to flat fields
    conf = event_data.pop("confidence", None) or {}
    event_data["confidence_event_detection"] = conf.get("event_detection")
    event_data["confidence_event_type"] = conf.get("event_type")
    event_data["confidence_subject"] = conf.get("subject")
    event_data["confidence_temporal"] = conf.get("temporal")
    event_data["confidence_identity"] = conf.get("identity")
    event_data["confidence_severity"] = conf.get("severity")
    event_data["confidence_status"] = conf.get("status")
    event_data["confidence_outcome"] = conf.get("outcome")
    event_data["confidence_overall"] = conf.get("overall")

    # Map state_impacts to JSON
    # (state_impacts is already a list of dicts, stored as JSONList)

    db_event = CareEvent(**event_data)
    db.add(db_event)
    db.commit()
    db.refresh(db_event)

    # Run deduplication check
    dedup_results = dedup_service.deduplicate_mentions(
        db=db,
        person_id=event.person_id,
        new_event_id=db_event.id,
    )

    # If we found same-event duplicates, propose deduplication
    for dedup_result in dedup_results:
        if dedup_result.status == EventMentionDeduplicationStatus.SAME_EVENT:
            dedup_service.propose_deduplication(
                db=db,
                mention_a_id=db_event.id,
                mention_b_id=dedup_result.mention_b_id,
                proposed_by_caregiver_id=event.created_by_caregiver_id,
            )

    # Infer relationships
    inferences = relationship_service.infer_relationships(
        db=db,
        person_id=event.person_id,
        event_id=db_event.id,
    )
    for inference in inferences:
        try:
            relationship_service._create_relationship_from_inference(
                db=db,
                inference=inference,
                created_by=event.created_by_caregiver_id,
            )
        except ValueError:
            continue

    # Invalidate cache for this person's care events and groups
    try:
        cache_service.clear_pattern_sync(f"{event.person_id}:care_events:*")
        cache_service.clear_pattern_sync(f"{event.person_id}:care_event_groups:*")
        logger.debug(f"Invalidated care events and groups cache for person {event.person_id}")
    except Exception as e:
        logger.warning(f"Error invalidating care events cache: {e}")

    return db_event


@cached_response(ttl=300, key_prefix="care_events")
@router.get("/care-events", response_model=List[CareEventResponse])
def list_care_events(
    person_id: str = Query(..., description="Person ID"),
    event_type: Optional[CareEventType] = Query(default=None),
    status: Optional[CareEventStatus] = Query(default=None),
    severity: Optional[CareEventSeverity] = Query(default=None),
    lifecycle_stage: Optional[EventLifecycleStage] = Query(default=None),
    mention_type: Optional[EventMentionType] = Query(default=None),
    event_time_from: Optional[datetime] = Query(default=None),
    event_time_to: Optional[datetime] = Query(default=None),
    recurrence_group_id: Optional[str] = Query(default=None),
    group_id: Optional[str] = Query(default=None),
    situation_id: Optional[str] = Query(default=None),
    primary_evidence_id: Optional[str] = Query(default=None),
    has_relationships: Optional[bool] = Query(default=None),
    has_state_impacts: Optional[bool] = Query(default=None),
    quality_gate_passed: Optional[bool] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Search care events for a person."""
    query = db.query(CareEvent).filter(CareEvent.person_id == person_id)

    if event_type:
        query = query.filter(CareEvent.event_type == event_type)
    if status:
        query = query.filter(CareEvent.status == status)
    if severity:
        query = query.filter(CareEvent.severity == severity)
    if lifecycle_stage:
        query = query.filter(CareEvent.lifecycle_stage == lifecycle_stage)
    if mention_type:
        query = query.filter(CareEvent.mention_type == mention_type)
    if event_time_from:
        query = query.filter(CareEvent.event_time >= event_time_from)
    if event_time_to:
        query = query.filter(CareEvent.event_time <= event_time_to)
    if recurrence_group_id:
        query = query.filter(CareEvent.recurrence_group_id == recurrence_group_id)
    if group_id:
        query = query.filter(CareEvent.group_id == group_id)
    if situation_id:
        query = query.filter(CareEvent.situation_id == situation_id)
    if primary_evidence_id:
        query = query.filter(CareEvent.primary_evidence_id == primary_evidence_id)
    if has_relationships is not None:
        if has_relationships:
            query = query.filter(
                CareEvent.outgoing_relationships.any() | CareEvent.incoming_relationships.any()
            )
        else:
            query = query.filter(
                ~CareEvent.outgoing_relationships.any() & ~CareEvent.incoming_relationships.any()
            )
    if has_state_impacts is not None:
        subquery = db.query(CareEventStateImpact.care_event_id).filter(
            CareEventStateImpact.care_event_id == CareEvent.id
        )
        if has_state_impacts:
            query = query.filter(subquery.exists())
        else:
            query = query.filter(~subquery.exists())
    if quality_gate_passed is not None:
        query = query.filter(CareEvent.quality_gate_passed == quality_gate_passed)

    query = query.order_by(CareEvent.event_time.desc(), CareEvent.created_at.desc())
    query = query.limit(limit).offset(offset)

    return query.all()


@cached_response(ttl=3600, key_prefix="care_event")
@router.get("/care-events/{event_id}", response_model=CareEventResponse)
def get_care_event(
    event_id: str,
    include_superseded: bool = Query(default=False, description="Include superseded versions"),
    db: Session = Depends(get_db),
):
    """Get a single care event by ID."""
    event = db.get(CareEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Care event {event_id} not found")

    return event


@router.patch("/care-events/{event_id}", response_model=CareEventResponse)
def update_care_event(
    event_id: str,
    update: CareEventUpdate,
    db: Session = Depends(get_db),
):
    """Update a care event (limited fields)."""
    event = db.get(CareEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Care event {event_id} not found")

    update_data = update.model_dump(exclude_unset=True)

    # Map nested confidence
    if "confidence" in update_data:
        conf = update_data.pop("confidence")
        if conf:
            if "event_detection" in conf: event.confidence_event_detection = conf["event_detection"]
            if "event_type" in conf: event.confidence_event_type = conf["event_type"]
            if "subject" in conf: event.confidence_subject = conf["subject"]
            if "temporal" in conf: event.confidence_temporal = conf["temporal"]
            if "identity" in conf: event.confidence_identity = conf["identity"]
            if "severity" in conf: event.confidence_severity = conf["severity"]
            if "status" in conf: event.confidence_status = conf["status"]
            if "outcome" in conf: event.confidence_outcome = conf["outcome"]
            if "overall" in conf: event.confidence_overall = conf["overall"]
        continue_update = True
    else:
        continue_update = True

    if continue_update:
        for field_name, value in update_data.items():
            if hasattr(event, field_name):
                setattr(event, field_name, value)

    # Update nested confidence
    conf = update.model_dump(exclude_unset=True).get("confidence")
    if conf:
        for field_name, value in update_data.items():
            if hasattr(event, field_name):
                setattr(event, field_name, value)

    # Re-extract flat confidence
    if "confidence" in update.model_dump(exclude_unset=True):
        conf_dict = update.model_dump(exclude_unset=True)["confidence"]
        event.confidence_event_detection = conf_dict.get("event_detection")
        event.confidence_event_type = conf_dict.get("event_type")
        event.confidence_subject = conf_dict.get("subject")
        event.confidence_temporal = conf_dict.get("temporal")
        event.confidence_identity = conf_dict.get("identity")
        event.confidence_severity = conf_dict.get("severity")
        event.confidence_status = conf_dict.get("status")
        event.confidence_outcome = conf_dict.get("outcome")
        event.confidence_overall = conf_dict.get("overall")

    event.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(event)

    # Invalidate cache for this event and related caches
    try:
        person_id = event.person_id
        event_id = event.id
        # Invalidate care events list for this person
        cache_service.clear_pattern_sync(f"{person_id}:care_events:*")
        # Invalidate care event groups list for this person
        cache_service.clear_pattern_sync(f"{person_id}:care_event_groups:*")
        # Invalidate single event cache
        cache_service.clear_pattern_sync(f"{person_id}:care_event:{event_id}:*")
        # Invalidate event trace cache
        cache_service.clear_pattern_sync(f"{person_id}:care_event_trace:{event_id}:*")
        # Invalidate event graph cache
        cache_service.clear_pattern_sync(f"{person_id}:care_event_graph:{event_id}:*")
        # Invalidate event state impacts cache
        cache_service.clear_pattern_sync(f"{person_id}:care_event_state_impacts:{event_id}:*")
        # Invalidate event versions cache
        cache_service.clear_pattern_sync(f"{person_id}:care_event_versions:{event_id}:*")
        # Invalidate event current cache
        cache_service.clear_pattern_sync(f"{person_id}:care_event_current:{event_id}:*")
        # Invalidate event deduplications cache
        cache_service.clear_pattern_sync(f"{person_id}:care_event_dedups:{event_id}:*")
        logger.debug(f"Invalidated care event caches for person {person_id}, event {event_id}")
    except Exception as e:
        logger.warning(f"Error invalidating care event caches: {e}")

    return event


@router.delete("/care-events/{event_id}", response_model=CareEventInvalidationResponse)
def invalidate_care_event(
    event_id: str,
    reason: EventInvalidationReason = Query(..., description="Reason for invalidation"),
    reason_details: Optional[str] = Query(default=None),
    invalidated_by: str = Query(..., description="Caregiver ID invalidating"),
    db: Session = Depends(get_db),
):
    """Invalidate a care event."""
    invalidation = lifecycle_service.invalidate_event(
        db=db,
        care_event_id=event_id,
        reason=reason,
        invalidated_by=invalidated_by,
        reason_details=reason_details,
    )

    # Invalidate cache for this event and related caches
    try:
        event = db.get(CareEvent, event_id)
        if event:
            person_id = event.person_id
            event_id = event.id
            # Invalidate care events list for this person
            cache_service.clear_pattern_sync(f"{person_id}:care_events:*")
            # Invalidate care event groups list for this person
            cache_service.clear_pattern_sync(f"{person_id}:care_event_groups:*")
            # Invalidate single event cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event:{event_id}:*")
            # Invalidate event trace cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_trace:{event_id}:*")
            # Invalidate event graph cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_graph:{event_id}:*")
            # Invalidate event state impacts cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_state_impacts:{event_id}:*")
            # Invalidate event versions cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_versions:{event_id}:*")
            # Invalidate event current cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_current:{event_id}:*")
            # Invalidate event deduplications cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_dedups:{event_id}:*")
            logger.debug(f"Invalidated care event caches for person {person_id}, event {event_id}")
        else:
            logger.warning(f"Event {event_id} not found for cache invalidation")
    except Exception as e:
        logger.warning(f"Error invalidating care event caches: {e}")

    return invalidation


@router.post("/care-events/{event_id}/correct", response_model=CareEventCorrectionResponse)
def correct_care_event(
    event_id: str,
    correction: CareEventCorrectionCreate,
    db: Session = Depends(get_db),
):
    """Create and apply a correction to a care event."""
    # Create correction
    record = lifecycle_service.create_correction(
        db=db,
        care_event_id=event_id,
        correction_type=correction.correction_type,
        field_name=correction.field_name,
        old_value=correction.old_value,
        new_value=correction.new_value,
        corrected_by=correction.corrected_by,
        correction_reason=correction.correction_reason,
        correction_evidence_ids=correction.correction_evidence_ids,
        is_source_correction=correction.is_source_correction,
        source_correction_details=correction.source_correction_details,
    )

    # Apply correction
    if not correction.apply_immediately if hasattr(correction, 'apply_immediately') else True:
        record = lifecycle_service.apply_correction(db=db, correction_id=record.id)

    # Invalidate cache for this event and related caches
    try:
        event = db.get(CareEvent, record.care_event_id)
        if event:
            person_id = event.person_id
            event_id = event.id
            # Invalidate care events list for this person
            cache_service.clear_pattern_sync(f"{person_id}:care_events:*")
            # Invalidate care event groups list for this person
            cache_service.clear_pattern_sync(f"{person_id}:care_event_groups:*")
            # Invalidate single event cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event:{event_id}:*")
            # Invalidate event trace cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_trace:{event_id}:*")
            # Invalidate event graph cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_graph:{event_id}:*")
            # Invalidate event state impacts cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_state_impacts:{event_id}:*")
            # Invalidate event versions cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_versions:{event_id}:*")
            # Invalidate event current cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_current:{event_id}:*")
            # Invalidate event deduplications cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_dedups:{event_id}:*")
            logger.debug(f"Invalidated care event caches for person {person_id}, event {event_id}")
        else:
            logger.warning(f"Event {record.care_event_id} not found for cache invalidation")
    except Exception as e:
        logger.warning(f"Error invalidating care event caches: {e}")

    return record


@router.post("/care-events/{event_id}/supersede", response_model=CareEventSupersessionResponse)
def supersede_care_event(
    event_id: str,
    supersession: CareEventSupersessionCreate,
    db: Session = Depends(get_db),
):
    """Supersede one event with another."""
    record = lifecycle_service.supersede_event(
        db=db,
        superseded_event_id=event_id,
        superseding_event_id=supersession.superseding_event_id,
        reason=supersession.reason,
        established_by=supersession.established_by,
        is_explicit=supersession.is_explicit,
        reason_details=supersession.reason_details,
    )

    # Invalidate cache for both events and related caches
    try:
        # Invalidate superseded event caches
        superseded_event = db.get(CareEvent, event_id)
        if superseded_event:
            person_id = superseded_event.person_id
            superseded_event_id = superseded_event.id
            # Invalidate care events list for this person
            cache_service.clear_pattern_sync(f"{person_id}:care_events:*")
            # Invalidate care event groups list for this person
            cache_service.clear_pattern_sync(f"{person_id}:care_event_groups:*")
            # Invalidate single event cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event:{superseded_event_id}:*")
            # Invalidate event trace cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_trace:{superseded_event_id}:*")
            # Invalidate event graph cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_graph:{superseded_event_id}:*")
            # Invalidate event state impacts cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_state_impacts:{superseded_event_id}:*")
            # Invalidate event versions cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_versions:{superseded_event_id}:*")
            # Invalidate event current cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_current:{superseded_event_id}:*")
            # Invalidate event deduplications cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_dedups:{superseded_event_id}:*")
            logger.debug(f"Invalidated care event caches for superseded event person {person_id}, event {superseded_event_id}")
        # Invalidate superseding event caches
        superseding_event = db.get(CareEvent, supersession.superseding_event_id)
        if superseding_event:
            person_id = superseding_event.person_id
            superseding_event_id = superseding_event.id
            # Invalidate care events list for this person
            cache_service.clear_pattern_sync(f"{person_id}:care_events:*")
            # Invalidate care event groups list for this person
            cache_service.clear_pattern_sync(f"{person_id}:care_event_groups:*")
            # Invalidate single event cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event:{superseding_event_id}:*")
            # Invalidate event trace cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_trace:{superseding_event_id}:*")
            # Invalidate event graph cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_graph:{superseding_event_id}:*")
            # Invalidate event state impacts cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_state_impacts:{superseding_event_id}:*")
            # Invalidate event versions cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_versions:{superseding_event_id}:*")
            # Invalidate event current cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_current:{superseding_event_id}:*")
            # Invalidate event deduplications cache
            cache_service.clear_pattern_sync(f"{person_id}:care_event_dedups:{superseding_event_id}:*")
            logger.debug(f"Invalidated care event caches for superseding event person {person_id}, event {superseding_event_id}")
        else:
            logger.warning(f"Superseding event {supersession.superseding_event_id} not found for cache invalidation")
    except Exception as e:
        logger.warning(f"Error invalidating care event caches: {e}")

    return record


@router.post("/care-events/relationships", response_model=CareEventRelationshipResponse)
def create_relationship(
    rel: CareEventRelationshipCreate,
    db: Session = Depends(get_db),
):
    """Create a typed relationship between two care events."""
    relationship = lifecycle_service.create_relationship(
        db=db,
        source_event_id=rel.source_event_id,
        target_event_id=rel.target_event_id,
        relationship_type=rel.relationship_type,
        created_by=rel.created_by,
        is_temporal=rel.is_temporal,
        is_causal=rel.is_causal,
        causal_evidence_ids=rel.causal_evidence_ids,
        temporal_relationship=rel.temporal_relationship,
        time_gap_seconds=rel.time_gap_seconds,
        strength=rel.strength,
        confidence=rel.confidence,
        detection_method=rel.detection_method,
        detection_confidence=rel.detection_confidence,
        evidence_ids=rel.evidence_ids,
    )

    return relationship


@router.get("/care-events/relationships/{event_id}", response_model=List[CareEventRelationshipResponse])
def get_event_relationships(
    event_id: str,
    relationship_types: Optional[List[CareEventRelationshipType]] = Query(default=None),
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Get all relationships for an event."""
    return relationship_service.get_event_relationships(
        db=db,
        event_id=event_id,
        relationship_types=relationship_types,
        include_inactive=include_inactive,
    )


@router.post("/care-events/groups", response_model=CareEventGroupResponse)
def create_event_group(
    group: CareEventGroupCreate,
    db: Session = Depends(get_db),
):
    """Create an event group (episode, encounter, etc.)."""
    db_group = CareEventGroup(
        person_id=group.person_id,
        group_type=group.group_type,
        title=group.title,
        description=group.description,
        member_event_ids=group.member_event_ids,
        member_count=len(group.member_event_ids),
        grouping_reason=group.grouping_reason,
        grouping_confidence=0.8,
        is_active=True,
        status="active",
    )
    db.add(db_group)
    db.commit()
    db.refresh(db_group)

    return db_group


@cached_response(ttl=3600, key_prefix="care_event_groups")
@router.get("/care-events/groups/{person_id}", response_model=List[CareEventGroupResponse])
def get_event_groups(
    person_id: str,
    group_type: Optional[CareEventGroupingType] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Get event groups for a person."""
    query = db.query(CareEventGroup).filter(
        CareEventGroup.person_id == person_id,
        CareEventGroup.is_active == True,
    )
    if group_type:
        query = query.filter(CareEventGroup.group_type == group_type)
    return query.all()


@router.post("/care-events/recurrence", response_model=List[CareEventRecurrenceGroupResponse])
def detect_recurrence(
    person_id: str = Query(..., description="Person ID"),
    event_type: Optional[CareEventType] = Query(default=None),
    min_occurrences: int = Query(default=2, ge=2),
    create_groups: bool = Query(default=True, description="Create recurrence groups in database"),
    db: Session = Depends(get_db),
):
    """Detect recurrence patterns in care events."""
    patterns = recurrence_service.detect_recurrence_patterns(
        db=db,
        person_id=person_id,
        event_type=event_type,
        min_occurrences=min_occurrences,
    )

    groups = []
    for pattern in patterns:
        if create_groups:
            group = recurrence_service.create_recurrence_group(
                db=db,
                pattern=pattern,
                event_type=pattern.event_type if hasattr(pattern, 'event_type') else None,
                event_subtype=None,
                created_by_caregiver_id=None,
            )
            groups.append(group)

    # Convert patterns to response format
    return [
        CareEventRecurrenceGroupResponse(
            id=g.id if groups else f"pattern_{i}",
            person_id=person_id,
            event_type=event_type or CareEventType.OTHER_CARE_EVENT,
            event_subtype=None,
            first_occurrence_time=pattern.first_occurrence,
            last_occurrence_time=pattern.last_occurrence,
            occurrence_count=len(pattern.occurrences),
            pattern_type=pattern.pattern_type,
            average_interval_days=pattern.average_interval_days,
            interval_std_dev_days=pattern.interval_std_dev_days,
            frequency_per_month=pattern.frequency_per_month,
            trend=pattern.trend.value if hasattr(pattern.trend, 'value') else str(pattern.trend),
            pattern_confidence=pattern.confidence,
            grouping_confidence=pattern.confidence,
            detection_method=pattern.detection_method,
            detection_evidence_ids=pattern.evidence_ids,
            is_active=True,
            status="active",
            created_at=datetime.now(timezone.utc),
            updated_at=None,
            representative_event_id=None,
        )
        for i, pattern in enumerate(patterns)
    ]


@router.post("/care-events/state-impacts", response_model=CareEventStateImpactResponse)
def create_state_impact(
    impact: CareEventStateImpactCreate,
    db: Session = Depends(get_db),
):
    """Create an event-to-state impact record."""
    result = state_impact_service.create_state_impact(
        db=db,
        event_id=impact.care_event_id,
        state_type=impact.state_type,
        state_subject_id=impact.state_subject_id,
        state_subject_role=impact.state_subject_role,
        impact_type=impact.impact_type,
        impact_confidence=impact.impact_confidence,
        impact_details=impact.impact_details,
        supporting_evidence_ids=impact.supporting_evidence_ids,
        supporting_care_event_ids=impact.supporting_care_event_ids,
        reasoning_rule=impact.reasoning_rule,
        reasoning_confidence=impact.reasoning_confidence,
        temporal_relationship=impact.temporal_relationship,
        time_lag_days=impact.time_lag_days,
    )

    return result


@router.post("/care-events/{event_id}/infer-state-impacts", response_model=List[CareEventStateImpactResponse])
def infer_state_impacts(
    event_id: str,
    db: Session = Depends(get_db),
):
    """Infer state impacts for an event based on rules."""
    event = db.get(CareEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Care event {event_id} not found")

    inferences = state_impact_service.infer_state_impacts(db=db, event=event)

    results = []
    for inference in inferences:
        impact = CareEventStateImpact(
            care_event_id=event_id,
            state_type=inference.state_type,
            state_subject_id=inference.state_subject_id or event.subject_id or event.person_id,
            state_subject_role=EventSubjectRole.CARE_RECIPIENT,
            impact_type=inference.impact_type,
            impact_confidence=inference.impact_confidence,
            impact_details=inference.impact_details,
            supporting_evidence_ids=inference.supporting_evidence_ids,
            supporting_care_event_ids=inference.supporting_care_event_ids,
            reasoning_rule=inference.reasoning_rule,
            reasoning_confidence=inference.reasoning_confidence,
            temporal_relationship=inference.temporal_relationship,
            time_lag_days=inference.time_lag_days,
            validated_by_state_engine=False,
            is_revoked=False,
        )
        db.add(impact)
        db.commit()
        db.refresh(impact)
        results.append(impact)

    return results


@cached_response(ttl=1800, key_prefix="care_event_state_impacts")
@router.get("/care-events/{event_id}/state-impacts", response_model=List[CareEventStateImpactResponse])
def get_event_state_impacts(
    event_id: str,
    include_revoked: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Get state impacts for an event."""
    return state_impact_service.get_event_state_impacts(
        db=db,
        event_id=event_id,
        include_revoked=include_revoked,
    )


@router.post("/care-events/dedup/propose", response_model=CareEventMentionDeduplicationResponse)
def propose_deduplication(
    payload: CareEventMentionDeduplicationCreate = Body(...),
    proposed_by: str = Query(..., description="Caregiver ID proposing"),
    db: Session = Depends(get_db),
):
    """Propose a deduplication for two event mentions."""
    dedup = dedup_service.propose_deduplication(
        db=db,
        mention_a_id=payload.mention_a_id,
        mention_b_id=payload.mention_b_id,
        proposed_by_caregiver_id=proposed_by,
    )

    if payload.similarity_score is not None:
        dedup.similarity_score = payload.similarity_score
    if payload.match_criteria is not None:
        dedup.match_criteria = payload.match_criteria
    if payload.match_details is not None:
        dedup.match_details = payload.match_details

    db.commit()
    db.refresh(dedup)

    return dedup


@router.post("/care-events/dedup/resolve", response_model=CareEventMentionDeduplicationResponse)
def resolve_deduplication(
    dedup_id: str = Query(..., description="Deduplication record ID"),
    status: EventMentionDeduplicationStatus = Query(..., description="Resolution status"),
    resolved_event_id: Optional[str] = Query(default=None, description="Canonical event if SAME_EVENT"),
    resolution_reason: Optional[str] = Query(default=None),
    confirmed_by: str = Query(..., description="Caregiver ID confirming"),
    db: Session = Depends(get_db),
):
    """Resolve a deduplication proposal."""
    result = dedup_service.resolve_deduplication(
        db=db,
        dedup_id=dedup_id,
        status=status,
        resolved_event_id=resolved_event_id,
        resolution_reason=resolution_reason,
        confirmed_by_caregiver_id=confirmed_by,
    )
    return result


@cached_response(ttl=3600, key_prefix="care_event_trace")
@router.get("/care-events/{event_id}/trace", response_model=CareEventTraceResponse)
def get_event_trace(
    event_id: str,
    db: Session = Depends(get_db),
):
    """Get full traceability chain for a care event."""
    event = db.get(CareEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Care event {event_id} not found")

    # Build trace response
    evidence_chain = []
    if event.evidence_ids:
        from app.models.evidence import Evidence
        evidence_items = db.query(Evidence).filter(Evidence.id.in_(event.evidence_ids)).all()
        for ev in evidence_items:
            evidence_chain.append({
                "id": ev.id,
                "type": ev.type.value if hasattr(ev.type, 'value') else str(ev.type),
                "source_text": ev.source_text,
                "negation_status": ev.negation_status.value if ev.negation_status else None,
                "uncertainty_level": ev.uncertainty_level.value if ev.uncertainty_level else None,
                "confidence_overall": ev.confidence_overall,
                "created_at": ev.created_at.isoformat(),
            })

    # Source documents
    source_documents = []
    if event.source_ids:
        for sid in event.source_ids:
            # Would fetch the actual document
            source_documents.append({
                "id": sid,
                "type": "document",
            })

    # Versions
    versions = db.query(CareEventVersion).filter(
        CareEventVersion.care_event_id == event_id
    ).order_by(CareEventVersion.version).all()

    # Relationships
    relationships = db.query(CareEventRelationship).filter(
        (CareEventRelationship.source_event_id == event_id)
        | (CareEventRelationship.target_event_id == event_id)
    ).all()

    # Corrections
    corrections = db.query(CareEventCorrection).filter(
        CareEventCorrection.care_event_id == event_id
    ).all()

    # Invalidations
    invalidations = db.query(CareEventInvalidation).filter(
        CareEventInvalidation.care_event_id == event_id
    ).all()

    # Supersessions
    supersessions = db.query(CareEventSupersession).filter(
        (CareEventSupersession.superseded_event_id == event_id)
        | (CareEventSupersession.superseding_event_id == event_id)
    ).all()

    # Mention deduplications
    dedups = db.query(CareEventMentionDeduplication).filter(
        (CareEventMentionDeduplication.mention_a_id == event_id)
        | (CareEventMentionDeduplication.mention_b_id == event_id)
    ).all()

    # State impacts
    state_impacts = db.query(CareEventStateImpact).filter(
        CareEventStateImpact.care_event_id == event_id
    ).all()

    # Recurrence group
    recurrence_group = None
    if event.recurrence_group_id:
        rg = db.get(CareEventRecurrenceGroup, event.recurrence_group_id)
        if rg:
            recurrence_group = {
                "id": rg.id,
                "event_type": rg.event_type.value if hasattr(rg.event_type, 'value') else str(rg.event_type),
                "occurrence_count": rg.occurrence_count,
                "average_interval_days": rg.average_interval_days,
                "trend": rg.trend,
            }

    # Event group
    group = None
    if event.group_id:
        g = db.get(CareEventGroup, event.group_id)
        if g:
            group = {
                "id": g.id,
                "group_type": g.group_type.value if hasattr(g.group_type, 'value') else str(g.group_type),
                "title": g.title,
            }

    trace_response = {
        "event": event,
        "evidence_chain": evidence_chain,
        "source_documents": source_documents,
        "versions": [
            {
                "id": v.id,
                "care_event_id": v.care_event_id,
                "version": v.version,
                "change_type": v.change_type,
                "change_reason": v.change_reason,
                "changed_by": v.changed_by,
                "changed_by_system": v.changed_by_system,
                "affected_downstream": v.affected_downstream or [],
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ],
        "relationships": [
            {
                "id": r.id,
                "source_event_id": r.source_event_id,
                "target_event_id": r.target_event_id,
                "relationship_type": r.relationship_type.value if hasattr(r.relationship_type, 'value') else str(r.relationship_type),
                "is_temporal": r.is_temporal,
                "is_causal": r.is_causal,
                "causal_evidence_ids": r.causal_evidence_ids or [],
                "temporal_relationship": r.temporal_relationship,
                "time_gap_seconds": r.time_gap_seconds,
                "confidence": r.confidence,
                "detection_method": r.detection_method,
                "is_active": r.is_active,
                "created_at": r.created_at.isoformat(),
            }
            for r in relationships
        ],
        "corrections": [
            {
                "id": c.id,
                "care_event_id": c.care_event_id,
                "correction_type": c.correction_type.value if hasattr(c.correction_type, 'value') else str(c.correction_type),
                "field_name": c.field_name,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "correction_reason": c.correction_reason,
                "is_source_correction": c.is_source_correction,
                "applied": c.applied,
                "applied_at": c.applied_at.isoformat() if c.applied_at else None,
                "corrected_by": c.corrected_by,
                "created_at": c.created_at.isoformat(),
            }
            for c in corrections
        ],
        "invalidations": [
            {
                "id": i.id,
                "care_event_id": i.care_event_id,
                "reason": i.reason.value if hasattr(i.reason, 'value') else str(i.reason),
                "reason_details": i.reason_details,
                "propagation_status": i.propagation_status,
                "affected_relationships": i.affected_relationships or [],
                "affected_situations": i.affected_situations or [],
                "invalidated_by": i.invalidated_by,
                "created_at": i.created_at.isoformat(),
                "completed_at": i.completed_at.isoformat() if i.completed_at else None,
            }
            for i in invalidations
        ],
        "supersessions": [
            {
                "id": s.id,
                "superseded_event_id": s.superseded_event_id,
                "superseding_event_id": s.superseding_event_id,
                "reason": s.reason.value if hasattr(s.reason, 'value') else str(s.reason),
                "effective_from": s.effective_from.isoformat(),
                "is_explicit": s.is_explicit,
                "established_by": s.established_by,
            }
            for s in supersessions
        ],
        "mention_deduplications": [
            {
                "id": d.id,
                "mention_a_id": d.mention_a_id,
                "mention_b_id": d.mention_b_id,
                "similarity_score": d.similarity_score,
                "status": d.status.value if hasattr(d.status, 'value') else str(d.status),
                "resolved_event_id": d.resolved_event_id,
                "confirmed_by": d.confirmed_by,
            }
            for d in dedups
        ],
        "recurrence_group": recurrence_group,
        "group": group,
        "state_impacts": [
            {
                "id": si.id,
                "state_type": si.state_type,
                "impact_type": si.impact_type.value if hasattr(si.impact_type, 'value') else str(si.impact_type),
                "impact_confidence": si.impact_confidence.value if hasattr(si.impact_confidence, 'value') else str(si.impact_confidence),
                "reasoning_rule": si.reasoning_rule,
                "reasoning_confidence": si.reasoning_confidence,
                "temporal_relationship": si.temporal_relationship,
                "time_lag_days": si.time_lag_days,
                "validated_by_state_engine": si.validated_by_state_engine,
                "is_revoked": si.is_revoked,
                "supporting_evidence_ids": si.supporting_evidence_ids or [],
                "created_at": si.created_at.isoformat(),
            }
            for si in state_impacts
        ],
        "downstream_situations": [],
        "downstream_changes": [],
        "downstream_watches": [],
    }

    return trace_response


@cached_response(ttl=1800, key_prefix="care_event_graph")
@router.get("/care-events/{event_id}/graph", response_model=CareEventGraphResponse)
def get_event_graph(
    person_id: str = Query(..., description="Person ID"),
    include_recurrence: bool = Query(default=True),
    include_groups: bool = Query(default=True),
    include_state_impacts: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Get event relationship graph for a person."""
    graph = relationship_service.get_relationship_graph(
        db=db,
        person_id=person_id,
    )

    nodes = db.query(CareEvent).filter(CareEvent.person_id == person_id).all()

    recurrence_groups = []
    if include_recurrence:
        rec_groups = db.query(CareEventRecurrenceGroup).filter(
            CareEventRecurrenceGroup.person_id == person_id,
            CareEventRecurrenceGroup.is_active == True,
        ).all()
        recurrence_groups = [
            {
                "id": g.id,
                "person_id": g.person_id,
                "event_type": g.event_type.value if hasattr(g.event_type, 'value') else str(g.event_type),
                "event_subtype": g.event_subtype.value if g.event_subtype and hasattr(g.event_subtype, 'value') else (g.event_subtype or None),
                "first_occurrence_time": g.first_occurrence_time.isoformat() if g.first_occurrence_time else None,
                "last_occurrence_time": g.last_occurrence_time.isoformat() if g.last_occurrence_time else None,
                "occurrence_count": g.occurrence_count,
                "pattern_type": g.pattern_type,
                "average_interval_days": g.average_interval_days,
                "interval_std_dev_days": g.interval_std_dev_days,
                "frequency_per_month": g.frequency_per_month,
                "trend": g.trend,
                "pattern_confidence": g.pattern_confidence,
                "grouping_confidence": g.grouping_confidence,
                "detection_method": g.detection_method.value if g.detection_method and hasattr(g.detection_method, 'value') else str(g.detection_method),
                "detection_evidence_ids": g.detection_evidence_ids or [],
                "is_active": g.is_active,
                "status": g.status,
                "representative_event_id": g.representative_event_id,
                "created_at": g.created_at.isoformat(),
                "updated_at": g.updated_at.isoformat() if g.updated_at else None,
            }
            for g in rec_groups
        ]

    groups = []
    if include_groups:
        ev_groups = db.query(CareEventGroup).filter(
            CareEventGroup.person_id == person_id,
            CareEventGroup.is_active == True,
        ).all()
        groups = [
            {
                "id": g.id,
                "person_id": g.person_id,
                "group_type": g.group_type.value if hasattr(g.group_type, 'value') else str(g.group_type),
                "title": g.title,
                "description": g.description,
                "temporal_start": g.temporal_start.isoformat() if g.temporal_start else None,
                "temporal_end": g.temporal_end.isoformat() if g.temporal_end else None,
                "member_event_ids": g.member_event_ids or [],
                "member_count": g.member_count,
                "grouping_confidence": g.grouping_confidence,
                "grouping_reason": g.grouping_reason,
                "is_active": g.is_active,
                "status": g.status,
                "created_at": g.created_at.isoformat(),
                "updated_at": g.updated_at.isoformat() if g.updated_at else None,
            }
            for g in ev_groups
        ]

    return {
        "nodes": nodes,
        "edges": graph["temporal_edges"],
        "recurrence_groups": recurrence_groups,
        "groups": groups,
    }


@router.post("/care-events/historical-knowledge", response_model=HistoricalKnowledgeResponse)
def query_historical_knowledge(
    query: HistoricalKnowledgeQuery,
    db: Session = Depends(get_db),
):
    """Query what SolenOS believed about a person at a specific point in time.

    Implements historical reconstruction (Requirement 132).
    Returns events as they were known at that time, including which versions were current.
    """
    as_of = query.as_of
    person_id = query.person_id

    # Get all events for this person
    event_query = db.query(CareEvent).filter(
        CareEvent.person_id == person_id,
        CareEvent.created_at <= as_of,
    )
    if query.event_types:
        type_values = [et.value if hasattr(et, 'value') else str(et) for et in query.event_types]
        event_query = event_query.filter(CareEvent.event_type.in_(type_values))

    events = event_query.all()

    # Filter out superseded and invalidated events as of the query time
    active_events = []
    event_versions_used: Dict[str, int] = {}
    supersessions_effective: List[str] = []
    invalidations_effective: List[str] = []

    for event in events:
        # Check if invalidated before as_of
        if event.lifecycle_stage == EventLifecycleStage.INVALIDATED:
            if event.invalidated_at and event.invalidated_at <= as_of:
                invalidations_effective.append(event.id)
                if query.include_invalidated:
                    active_events.append(event)
                continue

        # Check if superseded before as_of
        if event.lifecycle_stage == EventLifecycleStage.SUPERSEDED:
            if event.superseded_by_event_id:
                superseding = db.get(CareEvent, event.superseded_by_event_id)
                if superseding and superseding.created_at <= as_of:
                    supersessions_effective.append(event.id)
                    # The superseding event should be in the active set
                    if superseding not in active_events:
                        active_events.append(superseding)
                    continue
            if query.include_superseded:
                active_events.append(event)
            continue

        active_events.append(event)
        event_versions_used[event.id] = event.version

    # Confidence notes
    confidence_notes = f"Historical reconstruction as of {as_of.isoformat()}. " \
                       f"{len(active_events)} active events, " \
                       f"{len(supersessions_effective)} superseded, " \
                       f"{len(invalidations_effective)} invalidated."

    return {
        "as_of": as_of,
        "person_id": person_id,
        "events": active_events,
        "event_versions_used": event_versions_used,
        "supersedions_effective": supersessions_effective,
        "invalidations_effective": invalidations_effective,
        "confidence_notes": confidence_notes,
    }


@cached_response(ttl=3600, key_prefix="care_event_versions")
@router.get("/care-events/{event_id}/versions", response_model=List[CareEventVersionResponse])
def get_event_versions(
    event_id: str,
    db: Session = Depends(get_db),
):
    """Get version history for a care event."""
    versions = db.query(CareEventVersion).filter(
        CareEventVersion.care_event_id == event_id
    ).order_by(CareEventVersion.version).all()

    return [
        {
            "id": v.id,
            "care_event_id": v.care_event_id,
            "version": v.version,
            "change_type": v.change_type,
            "change_reason": v.change_reason,
            "changed_by": v.changed_by,
            "changed_by_system": v.changed_by_system,
            "affected_downstream": v.affected_downstream or [],
            "created_at": v.created_at.isoformat(),
        }
        for v in versions
    ]


@cached_response(ttl=3600, key_prefix="care_event_dedups")
@router.get("/care-events/{event_id}/dedup", response_model=List[CareEventMentionDeduplicationResponse])
def get_event_deduplications(
    event_id: str,
    db: Session = Depends(get_db),
):
    """Get all deduplication records for an event."""
    return dedup_service.find_deduplication_for_event(db=db, event_id=event_id)


@cached_response(ttl=3600, key_prefix="care_event_current")
@router.get("/care-events/{event_id}/current", response_model=CareEventResponse)
def get_current_event_version(
    event_id: str,
    db: Session = Depends(get_db),
):
    """Get the current (non-superseded, non-invalidated) version of an event."""
    current = lifecycle_service.get_current_event(db=db, event_id=event_id)
    if not current:
        raise HTTPException(
            status_code=404,
            detail=f"No current version of event {event_id} (may be invalidated or superseded)",
        )
    return current
