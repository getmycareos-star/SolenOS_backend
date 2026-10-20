"""Longitudinal Evidence Service.

Provides analytics and trends over evidence lifecycle:
- Snapshots of evidence state at points in time
- Trend computation
- Significant event tracking
- Confidence evolution
- Relationship evolution
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

from sqlalchemy.orm import Session
from sqlalchemy import and_, desc, func

from app.models.longitudinal_evidence import (
    LongitudinalEvidenceSnapshot,
    LongitudinalEvidenceTrend,
    LongitudinalEvidenceEvent,
)
from app.models.evidence import Evidence, EvidenceRelationship, EvidenceVersion, EvidenceCorrection, EvidenceInvalidation, EvidenceSupersession
from app.models.care import Person


class LongitudinalEvidenceService:
    """Service for longitudinal evidence analytics."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ============================================================
    # SNAPSHOTS
    # ============================================================

    def create_snapshot(self, person_id: str, snapshot_time: datetime, period_type: str = "daily", period_label: Optional[str] = None) -> LongitudinalEvidenceSnapshot:
        """Create a snapshot of current evidence state."""
        evidence_list = self.db.query(Evidence).filter(Evidence.subject_id == person_id).all()
        evidence_ids = [e.id for e in evidence_list]

        total = len(evidence_list)
        active = sum(1 for e in evidence_list if e.status.value == "ACTIVE")
        invalidated = sum(1 for e in evidence_list if e.status.value == "INVALIDATED")
        superseded = sum(1 for e in evidence_list if e.status.value == "SUPERSEDED")
        corrected = sum(1 for e in evidence_list if e.status.value == "CORRECTED")

        by_type: Dict[str, int] = defaultdict(int)
        by_status: Dict[str, int] = defaultdict(int)
        conf_overall: List[float] = []
        conf_extraction: List[float] = []
        conf_entity: List[float] = []
        conf_temporal: List[float] = []
        conf_source: List[float] = []
        conf_interpretation: List[float] = []

        for e in evidence_list:
            by_type[e.type.value] += 1
            by_status[e.status.value] += 1
            if e.confidence_overall is not None:
                conf_overall.append(e.confidence_overall)
            if e.confidence_extraction is not None:
                conf_extraction.append(e.confidence_extraction)
            if e.confidence_entity_resolution is not None:
                conf_entity.append(e.confidence_entity_resolution)
            if e.confidence_temporal is not None:
                conf_temporal.append(e.confidence_temporal)
            if e.confidence_source_support is not None:
                conf_source.append(e.confidence_source_support)
            if e.confidence_interpretation is not None:
                conf_interpretation.append(e.confidence_interpretation)

        relationships = self.db.query(EvidenceRelationship).filter(
            EvidenceRelationship.source_evidence_id.in_(evidence_ids)
        ).all() if evidence_ids else []
        total_rels = len(relationships)
        rels_by_type: Dict[str, int] = defaultdict(int)
        for r in relationships:
            rels_by_type[r.relationship_type.value] += 1

        snapshot = LongitudinalEvidenceSnapshot(
            person_id=person_id,
            snapshot_time=snapshot_time,
            period_type=period_type,
            period_label=period_label,
            total_evidence_count=total,
            active_evidence_count=active,
            invalidated_evidence_count=invalidated,
            superseded_evidence_count=superseded,
            corrected_evidence_count=corrected,
            evidence_by_type=dict(by_type),
            evidence_by_status=dict(by_status),
            avg_confidence_overall=sum(conf_overall) / len(conf_overall) if conf_overall else None,
            avg_confidence_extraction=sum(conf_extraction) / len(conf_extraction) if conf_extraction else None,
            avg_confidence_entity_resolution=sum(conf_entity) / len(conf_entity) if conf_entity else None,
            avg_confidence_temporal=sum(conf_temporal) / len(conf_temporal) if conf_temporal else None,
            avg_confidence_source_support=sum(conf_source) / len(conf_source) if conf_source else None,
            avg_confidence_interpretation=sum(conf_interpretation) / len(conf_interpretation) if conf_interpretation else None,
            total_relationships=total_rels,
            relationships_by_type=dict(rels_by_type),
            evidence_ids=evidence_ids,
        )
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot

    def get_snapshots(self, person_id: str, period_type: Optional[str] = None, limit: int = 100) -> List[LongitudinalEvidenceSnapshot]:
        """Get snapshots for a person."""
        query = self.db.query(LongitudinalEvidenceSnapshot).filter(
            LongitudinalEvidenceSnapshot.person_id == person_id
        )
        if period_type:
            query = query.filter(LongitudinalEvidenceSnapshot.period_type == period_type)
        return query.order_by(desc(LongitudinalEvidenceSnapshot.snapshot_time)).limit(limit).all()

    def get_latest_snapshot(self, person_id: str, period_type: str = "daily") -> Optional[LongitudinalEvidenceSnapshot]:
        """Get the latest snapshot for a person and period type."""
        return self.db.query(LongitudinalEvidenceSnapshot).filter(
            LongitudinalEvidenceSnapshot.person_id == person_id,
            LongitudinalEvidenceSnapshot.period_type == period_type,
        ).order_by(desc(LongitudinalEvidenceSnapshot.snapshot_time)).first()

    # ============================================================
    # TRENDS
    # ============================================================

    def compute_trends(self, person_id: str, window_type: str = "weekly") -> List[LongitudinalEvidenceTrend]:
        """Compute trend metrics for all evidence types over time."""
        evidence_list = self.db.query(Evidence).filter(Evidence.subject_id == person_id).all()
        if not evidence_list:
            return []

        # Group evidence by type
        by_type: Dict[str, List[Evidence]] = defaultdict(list)
        for e in evidence_list:
            by_type[e.type.value].append(e)

        trends = []
        now = datetime.now(timezone.utc)

        for evidence_type, items in by_type.items():
            # Compute trends over windows
            if window_type == "weekly":
                window_size = timedelta(days=7)
                num_windows = 12  # 12 weeks
            elif window_type == "monthly":
                window_size = timedelta(days=30)
                num_windows = 12  # 12 months
            else:
                window_size = timedelta(days=1)
                num_windows = 30  # 30 days

            for i in range(num_windows):
                window_end = now - i * window_size
                window_start = window_end - window_size

                # Count evidence in this window
                window_evidence = [
                    e for e in items
                    if e.created_at is not None and window_start <= e.created_at < window_end
                ]

                if not window_evidence:
                    continue

                # Count status changes in window
                versions = self.db.query(EvidenceVersion).filter(
                    EvidenceVersion.evidence_id.in_([e.id for e in items]),
                    EvidenceVersion.created_at >= window_start,
                    EvidenceVersion.created_at < window_end,
                ).all()
                corrections = self.db.query(EvidenceCorrection).filter(
                    EvidenceCorrection.evidence_id.in_([e.id for e in items]),
                    EvidenceCorrection.created_at >= window_start,
                    EvidenceCorrection.created_at < window_end,
                ).all()
                invalidations = self.db.query(EvidenceInvalidation).filter(
                    EvidenceInvalidation.evidence_id.in_([e.id for e in items]),
                    EvidenceInvalidation.created_at >= window_start,
                    EvidenceInvalidation.created_at < window_end,
                ).all()

                # Compute confidence trend
                confidences = [e.confidence_overall for e in window_evidence if e.confidence_overall is not None]
                avg_conf = sum(confidences) / len(confidences) if confidences else None

                trend = LongitudinalEvidenceTrend(
                    person_id=person_id,
                    evidence_type=evidence_type,
                    window_start=window_start,
                    window_end=window_end,
                    window_type=window_type,
                    avg_confidence_change=None,  # Would need previous window to compute
                    status_change_count=len(versions),
                    correction_count=len(corrections),
                    invalidation_count=len(invalidations),
                    first_evidence_id=window_evidence[0].id if window_evidence else None,
                    last_evidence_id=window_evidence[-1].id if window_evidence else None,
                    first_seen=min(e.created_at for e in window_evidence if e.created_at) if window_evidence else None,
                    last_seen=max(e.created_at for e in window_evidence if e.created_at) if window_evidence else None,
                    data_points=len(window_evidence),
                )
                self.db.add(trend)

        self.db.commit()
        return trends

    def get_trends(self, person_id: str, evidence_type: Optional[str] = None, window_type: str = "weekly", limit: int = 100) -> List[LongitudinalEvidenceTrend]:
        """Get pre-computed trends for a person."""
        query = self.db.query(LongitudinalEvidenceTrend).filter(
            LongitudinalEvidenceTrend.person_id == person_id,
            LongitudinalEvidenceTrend.window_type == window_type,
        )
        if evidence_type:
            query = query.filter(LongitudinalEvidenceTrend.evidence_type == evidence_type)
        return query.order_by(desc(LongitudinalEvidenceTrend.window_start)).limit(limit).all()

    # ============================================================
    # EVENT TRACKING
    # ============================================================

    def record_event(
        self,
        person_id: str,
        evidence_id: str,
        event_type: str,
        event_category: str = "lifecycle",
        changed_fields: Optional[List[str]] = None,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None,
        triggered_by: Optional[str] = None,
        trigger_details: Optional[str] = None,
        downstream_impact_count: int = 0,
        affected_entities: Optional[List[str]] = None,
        event_time: Optional[datetime] = None,
    ) -> LongitudinalEvidenceEvent:
        """Record a significant event in evidence lifecycle."""
        event = LongitudinalEvidenceEvent(
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
            event_time=event_time or datetime.now(timezone.utc),
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def get_events(self, person_id: str, evidence_id: Optional[str] = None, event_type: Optional[str] = None, limit: int = 100) -> List[LongitudinalEvidenceEvent]:
        """Get lifecycle events for a person or specific evidence."""
        query = self.db.query(LongitudinalEvidenceEvent).filter(LongitudinalEvidenceEvent.person_id == person_id)
        if evidence_id:
            query = query.filter(LongitudinalEvidenceEvent.evidence_id == evidence_id)
        if event_type:
            query = query.filter(LongitudinalEvidenceEvent.event_type == event_type)
        return query.order_by(desc(LongitudinalEvidenceEvent.event_time)).limit(limit).all()

    # ============================================================
    # ANALYTICS
    # ============================================================

    def get_evidence_timeline(self, person_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """Get daily evidence counts over time."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        evidence = self.db.query(Evidence).filter(
            Evidence.subject_id == person_id,
            Evidence.created_at >= cutoff,
        ).order_by(Evidence.created_at).all()

        # Group by day
        daily: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "types": defaultdict(int), "statuses": defaultdict(int), "avg_confidence": []}
        )

        for e in evidence:
            day = e.created_at.strftime("%Y-%m-%d") if e.created_at else "unknown"
            daily[day]["count"] += 1
            daily[day]["types"][e.type.value] += 1
            daily[day]["statuses"][e.status.value] += 1
            if e.confidence_overall is not None:
                daily[day]["avg_confidence"].append(e.confidence_overall)

        result = []
        for day, data in sorted(daily.items()):
            avg_conf = sum(data["avg_confidence"]) / len(data["avg_confidence"]) if data["avg_confidence"] else None
            result.append({
                "day": day,
                "count": data["count"],
                "types": dict(data["types"]),
                "statuses": dict(data["statuses"]),
                "avg_confidence": avg_conf,
            })

        return result

    def get_type_distribution(self, person_id: str) -> List[Dict[str, Any]]:
        """Get distribution of evidence by type."""
        evidence = self.db.query(Evidence).filter(Evidence.subject_id == person_id).all()
        by_type: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "active": 0, "avg_confidence": []})
        for e in evidence:
            by_type[e.type.value]["count"] += 1
            if e.status.value == "ACTIVE":
                by_type[e.type.value]["active"] += 1
            if e.confidence_overall is not None:
                by_type[e.type.value]["avg_confidence"].append(e.confidence_overall)

        result = []
        for type_name, data in by_type.items():
            avg_conf = sum(data["avg_confidence"]) / len(data["avg_confidence"]) if data["avg_confidence"] else None
            result.append({
                "type": type_name,
                "count": data["count"],
                "active": data["active"],
                "avg_confidence": avg_conf,
            })
        return sorted(result, key=lambda x: x["count"], reverse=True)

    def get_confidence_evolution(self, person_id: str, evidence_type: Optional[str] = None, days: int = 30) -> List[Dict[str, Any]]:
        """Get confidence evolution over time."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = self.db.query(Evidence).filter(
            Evidence.subject_id == person_id,
            Evidence.created_at >= cutoff,
        )
        if evidence_type:
            query = query.filter(Evidence.type == evidence_type)
        evidence = query.order_by(Evidence.created_at).all()

        return [
            {
                "id": e.id,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "type": e.type.value,
                "status": e.status.value,
                "confidence_overall": e.confidence_overall,
                "confidence_extraction": e.confidence_extraction,
                "confidence_entity_resolution": e.confidence_entity_resolution,
                "confidence_temporal": e.confidence_temporal,
                "confidence_source_support": e.confidence_source_support,
                "confidence_interpretation": e.confidence_interpretation,
            }
            for e in evidence
        ]

    def get_relationship_evolution(self, person_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """Get relationship evolution over time."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        evidence_ids = [e.id for e in self.db.query(Evidence).filter(Evidence.subject_id == person_id).all()]

        if not evidence_ids:
            return []

        relationships = self.db.query(EvidenceRelationship).filter(
            EvidenceRelationship.source_evidence_id.in_(evidence_ids),
            EvidenceRelationship.created_at >= cutoff,
        ).order_by(EvidenceRelationship.created_at).all()

        return [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "source_evidence_id": r.source_evidence_id,
                "target_evidence_id": r.target_evidence_id,
                "relationship_type": r.relationship_type.value,
                "is_active": r.is_active,
                "detection_confidence": r.detection_confidence,
            }
            for r in relationships
        ]

    def get_quality_metrics(self, person_id: str, days: int = 30) -> Dict[str, Any]:
        """Get quality metrics over time."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        evidence = self.db.query(Evidence).filter(
            Evidence.subject_id == person_id,
            Evidence.created_at >= cutoff,
        ).all()

        total = len(evidence)
        quality_passed = sum(1 for e in evidence if e.quality_gate_passed)
        pass_rate = quality_passed / total if total > 0 else None

        avg_quality_score = None
        quality_scores = [e.quality_gate_details.get("score") for e in evidence if e.quality_gate_details and e.quality_gate_details.get("score") is not None]
        if quality_scores:
            avg_quality_score = sum(quality_scores) / len(quality_scores)

        return {
            "total_evidence": total,
            "quality_gate_passed": quality_passed,
            "quality_gate_pass_rate": pass_rate,
            "avg_quality_score": avg_quality_score,
            "period_days": days,
        }
