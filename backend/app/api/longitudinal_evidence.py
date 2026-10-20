"""FastAPI routes for Longitudinal Evidence Analytics API."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

from app.core.database import get_db
from app.services.longitudinal_evidence_service import LongitudinalEvidenceService
from app.models.longitudinal_evidence import LongitudinalEvidenceSnapshot, LongitudinalEvidenceTrend, LongitudinalEvidenceEvent


router = APIRouter(prefix="/longitudinal/evidence", tags=["longitudinal-evidence"])


def get_longitudinal_service(db: Session = Depends(get_db)) -> LongitudinalEvidenceService:
    return LongitudinalEvidenceService(db)


# ============================================================
# SNAPSHOTS
# ============================================================

@router.post("/snapshots")
def create_snapshot(
    person_id: str,
    snapshot_time: Optional[datetime] = None,
    period_type: str = Query("daily"),
    period_label: Optional[str] = None,
    service: LongitudinalEvidenceService = Depends(get_longitudinal_service),
):
    """Create a longitudinal evidence snapshot for a person."""
    if not snapshot_time:
        snapshot_time = datetime.now()
    snapshot = service.create_snapshot(person_id, snapshot_time, period_type, period_label)
    return {
        "id": snapshot.id,
        "person_id": snapshot.person_id,
        "snapshot_time": snapshot.snapshot_time.isoformat(),
        "period_type": snapshot.period_type,
        "total_evidence_count": snapshot.total_evidence_count,
        "active_evidence_count": snapshot.active_evidence_count,
        "avg_confidence_overall": snapshot.avg_confidence_overall,
    }


@router.get("/snapshots/{person_id}")
def get_snapshots(
    person_id: str,
    period_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    service: LongitudinalEvidenceService = Depends(get_longitudinal_service),
):
    """Get longitudinal evidence snapshots for a person."""
    snapshots = service.get_snapshots(person_id, period_type, limit)
    return [
        {
            "id": s.id,
            "snapshot_time": s.snapshot_time.isoformat(),
            "period_type": s.period_type,
            "period_label": s.period_label,
            "total_evidence_count": s.total_evidence_count,
            "active_evidence_count": s.active_evidence_count,
            "invalidated_evidence_count": s.invalidated_evidence_count,
            "superseded_evidence_count": s.superseded_evidence_count,
            "corrected_evidence_count": s.corrected_evidence_count,
            "evidence_by_type": s.evidence_by_type,
            "evidence_by_status": s.evidence_by_status,
            "avg_confidence_overall": s.avg_confidence_overall,
            "avg_confidence_extraction": s.avg_confidence_extraction,
            "avg_confidence_entity_resolution": s.avg_confidence_entity_resolution,
            "avg_confidence_temporal": s.avg_confidence_temporal,
            "avg_confidence_source_support": s.avg_confidence_source_support,
            "avg_confidence_interpretation": s.avg_confidence_interpretation,
            "total_relationships": s.total_relationships,
            "relationships_by_type": s.relationships_by_type,
            "avg_quality_gate_score": s.avg_quality_gate_score,
            "quality_gate_pass_rate": s.quality_gate_pass_rate,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in snapshots
    ]


# ============================================================
# TRENDS
# ============================================================

@router.post("/trends/compute")
def compute_trends(
    person_id: str,
    window_type: str = Query("weekly"),
    service: LongitudinalEvidenceService = Depends(get_longitudinal_service),
):
    """Compute longitudinal evidence trends for a person."""
    trends = service.compute_trends(person_id, window_type)
    return {
        "person_id": person_id,
        "window_type": window_type,
        "trends_computed": len(trends),
        "trends": [
            {
                "id": t.id,
                "evidence_type": t.evidence_type.value,
                "window_start": t.window_start.isoformat(),
                "window_end": t.window_end.isoformat(),
                "count_trend": t.count_trend,
                "count_change_pct": t.count_change_pct,
                "confidence_trend": t.confidence_trend,
                "avg_confidence_change": t.avg_confidence_change,
                "status_change_count": t.status_change_count,
                "relationship_change_count": t.relationship_change_count,
                "correction_count": t.correction_count,
                "invalidation_count": t.invalidation_count,
                "data_points": t.data_points,
            }
            for t in trends
        ],
    }


@router.get("/trends/{person_id}")
def get_trends(
    person_id: str,
    evidence_type: Optional[str] = None,
    window_type: str = Query("weekly"),
    limit: int = Query(100, ge=1, le=1000),
    service: LongitudinalEvidenceService = Depends(get_longitudinal_service),
):
    """Get pre-computed longitudinal evidence trends."""
    trends = service.get_trends(person_id, evidence_type, window_type, limit)
    return [
        {
            "id": t.id,
            "evidence_type": t.evidence_type.value,
            "window_start": t.window_start.isoformat(),
            "window_end": t.window_end.isoformat(),
            "window_type": t.window_type,
            "count_trend": t.count_trend,
            "count_change_pct": t.count_change_pct,
            "confidence_trend": t.confidence_trend,
            "avg_confidence_change": t.avg_confidence_change,
            "status_change_count": t.status_change_count,
            "relationship_change_count": t.relationship_change_count,
            "correction_count": t.correction_count,
            "invalidation_count": t.invalidation_count,
            "data_points": t.data_points,
            "first_seen": t.first_seen.isoformat() if t.first_seen else None,
            "last_seen": t.last_seen.isoformat() if t.last_seen else None,
        }
        for t in trends
    ]


# ============================================================
# EVENTS
# ============================================================

@router.post("/events/record")
def record_event(
    person_id: str,
    evidence_id: str,
    event_type: str,
    event_category: str = Query("lifecycle"),
    changed_fields: Optional[list] = None,
    old_values: Optional[dict] = None,
    new_values: Optional[dict] = None,
    triggered_by: Optional[str] = None,
    trigger_details: Optional[str] = None,
    downstream_impact_count: int = Query(0),
    affected_entities: Optional[list] = None,
    event_time: Optional[datetime] = None,
    service: LongitudinalEvidenceService = Depends(get_longitudinal_service),
):
    """Record a longitudinal evidence event."""
    event = service.record_event(
        person_id=person_id,
        evidence_id=evidence_id,
        event_type=event_type,
        event_category=event_category,
        changed_fields=changed_fields,
        old_values=old_values,
        new_values=new_values,
        triggered_by=triggered_by,
        trigger_details=trigger_details,
        downstream_impact_count=downstream_impact_count,
        affected_entities=affected_entities,
        event_time=event_time,
    )
    return {
        "id": event.id,
        "person_id": event.person_id,
        "evidence_id": event.evidence_id,
        "event_type": event.event_type,
        "event_category": event.event_category,
        "event_time": event.event_time.isoformat(),
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.get("/events/{person_id}")
def get_events(
    person_id: str,
    evidence_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    service: LongitudinalEvidenceService = Depends(get_longitudinal_service),
):
    """Get longitudinal evidence events for a person."""
    events = service.get_events(person_id, evidence_id, event_type, limit)
    return [
        {
            "id": e.id,
            "evidence_id": e.evidence_id,
            "event_type": e.event_type,
            "event_category": e.event_category,
            "changed_fields": e.changed_fields,
            "old_values": e.old_values,
            "new_values": e.new_values,
            "triggered_by": e.triggered_by,
            "trigger_details": e.trigger_details,
            "downstream_impact_count": e.downstream_impact_count,
            "affected_entities": e.affected_entities,
            "event_time": e.event_time.isoformat(),
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


# ============================================================
# ANALYTICS
# ============================================================

@router.get("/timeline/{person_id}")
def get_evidence_timeline(
    person_id: str,
    days: int = Query(30, ge=1, le=365),
    service: LongitudinalEvidenceService = Depends(get_longitudinal_service),
):
    """Get daily evidence counts over time."""
    timeline = service.get_evidence_timeline(person_id, days)
    return {
        "person_id": person_id,
        "days": days,
        "timeline": timeline,
    }


@router.get("/distribution/{person_id}")
def get_evidence_distribution(
    person_id: str,
    service: LongitudinalEvidenceService = Depends(get_longitudinal_service),
):
    """Get distribution of evidence by type."""
    distribution = service.get_type_distribution(person_id)
    return {
        "person_id": person_id,
        "distribution": distribution,
    }


@router.get("/confidence-evolution/{person_id}")
def get_confidence_evolution(
    person_id: str,
    evidence_type: Optional[str] = None,
    days: int = Query(30, ge=1, le=365),
    service: LongitudinalEvidenceService = Depends(get_longitudinal_service),
):
    """Get confidence evolution over time."""
    evolution = service.get_confidence_evolution(person_id, evidence_type, days)
    return {
        "person_id": person_id,
        "evidence_type": evidence_type,
        "days": days,
        "evolution": evolution,
    }


@router.get("/relationship-evolution/{person_id}")
def get_relationship_evolution(
    person_id: str,
    days: int = Query(30, ge=1, le=365),
    service: LongitudinalEvidenceService = Depends(get_longitudinal_service),
):
    """Get relationship evolution over time."""
    evolution = service.get_relationship_evolution(person_id, days)
    return {
        "person_id": person_id,
        "days": days,
        "evolution": evolution,
    }


@router.get("/quality-metrics/{person_id}")
def get_quality_metrics(
    person_id: str,
    days: int = Query(30, ge=1, le=365),
    service: LongitudinalEvidenceService = Depends(get_longitudinal_service),
):
    """Get quality metrics for evidence."""
    metrics = service.get_quality_metrics(person_id, days)
    return {
        "person_id": person_id,
        "metrics": metrics,
    }
