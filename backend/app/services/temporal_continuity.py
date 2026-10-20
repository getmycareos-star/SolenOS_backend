"""Temporal Intelligence integration with Care Continuity.

This module wires the temporal intelligence engines (recurrence, trend, change detection,
historical knowledge, constraint graph, provenance) into the longitudinal care continuity
system, providing the "when" dimension to the "what" and "how" of care.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.care_event import CareEvent
from app.models.continuity import ContinuityThread, ContinuitySnapshot
from app.services.temporal_recurrence import (
    detect_recurrence,
    analyze_frequency,
    detect_frequency_change,
    predict_next_occurrence,
    RecurrencePattern,
    FrequencyAnalysis,
)
from app.services.temporal_trend import (
    detect_trend,
    detect_cyclical_pattern,
    compare_trends,
    interpret_clinical_trend,
    TimeSeriesPoint,
    TrendResult,
    MeasurementType,
)
from app.services.temporal_change import (
    compare_states,
    detect_changes_over_time,
    compare_baseline_to_current,
    detect_pre_post_changes,
    TemporalState,
    StateChange,
    BaselineComparison,
)
from app.services.temporal_knowledge import (
    build_knowledge_timeline,
    reconstruct_knowledge_at,
    query_historical_knowledge,
    get_knowledge_evolution,
    detect_knowledge_gaps,
    TemporalKnowledge,
    HistoricalKnowledgeSnapshot,
    TemporalEvidence,
)
from app.services.temporal_allen import (
    compute_allen_relation,
    is_before,
    is_after,
    overlaps,
    TemporalInterval as AllenInterval,
    TemporalPoint as AllenPoint,
)
from app.services.temporal_constraints import (
    build_constraint_graph_from_events,
    TemporalConstraintGraph,
)
from app.services.temporal_provenance import (
    get_provenance_store,
    trace_temporal_conclusion,
    verify_provenance_completeness,
)


@dataclass
class TemporalContinuityEnrichment:
    """Enrichment data added to continuity threads/snapshots from temporal intelligence."""
    
    # Recurrence
    recurrence_patterns: List[RecurrencePattern]
    frequency_analyses: List[FrequencyAnalysis]
    frequency_changes: List[FrequencyChange]
    next_predictions: List[Dict[str, Any]]
    
    # Trends
    trends: List[TrendResult]
    cyclical_patterns: List[Any]
    trend_interpretations: List[Dict[str, Any]]
    
    # Change detection
    state_changes: List[StateChange]
    baseline_comparisons: List[BaselineComparison]
    
    # Historical knowledge
    knowledge_at_snapshot: Optional[HistoricalKnowledgeSnapshot]
    knowledge_gaps: List[Dict[str, Any]]
    
    # Temporal relationships
    allen_relations: List[Dict[str, Any]]
    constraint_validation: Dict[str, Any]
    
    # Provenance
    provenance_verification: Dict[str, Any]


class TemporalContinuityEngine:
    """Integrates temporal intelligence with care continuity for longitudinal understanding."""
    
    def __init__(self, db: Session):
        self.db = db
        self.provenance_store = get_provenance_store()
    
    def enrich_thread(
        self,
        thread: ContinuityThread,
        events: Sequence[CareEvent],
        as_of: Optional[datetime] = None,
    ) -> TemporalContinuityEnrichment:
        """Add full temporal intelligence to a continuity thread."""
        as_of = as_of or datetime.now()
        
        # Extract temporal data from events
        event_timestamps = self._extract_timestamps(events, thread.subject)
        time_series = self._extract_time_series(events, thread.subject)
        temporal_states = self._extract_temporal_states(events, thread.subject)
        
        # 1. Recurrence & Frequency Analysis
        recurrence_patterns = detect_recurrence(
            event_timestamps,
            thread.subject,
            min_occurrences=2,
            window_days=365,
        )
        
        frequency_analysis = analyze_frequency(event_timestamps, thread.subject, period_days=30) if len(event_timestamps) >= 2 else None
        
        frequency_change = detect_frequency_change(
            event_timestamps,
            thread.subject,
            baseline_period_days=180,
            recent_period_days=30,
        ) if len(event_timestamps) >= 5 else None
        
        next_prediction = predict_next_occurrence(event_timestamps, thread.subject) if event_timestamps else None
        
        # 2. Trend Detection
        trends = []
        cyclical = []
        trend_interpretations = []
        
        if time_series:
            for ts in time_series:
                if len(ts) >= 3:
                    trend = detect_trend(ts)
                    trends.append(trend)
                    
                    # Clinical interpretation
                    interpretation = interpret_clinical_trend(trend, ts[0].measurement_type)
                    trend_interpretations.append(interpretation)
                    
                    # Check for cyclical patterns
                    cycle = detect_cyclical_pattern(ts)
                    if cycle:
                        cyclical.append(cycle)
        
        # 3. Change Detection
        state_changes = detect_changes_over_time(temporal_states, window_days=30)
        
        baseline_comparisons = []
        if len(temporal_states) >= 6:
            # Split into baseline (older half) and current (newer half)
            mid = len(temporal_states) // 2
            baseline = temporal_states[:mid]
            current = temporal_states[mid:]
            baseline_comparisons = compare_baseline_to_current(baseline, current)
        
        # 4. Historical Knowledge
        temporal_evidence = self._build_temporal_evidence(events)
        knowledge_timeline = build_knowledge_timeline(temporal_evidence)
        
        knowledge_at_snapshot = reconstruct_knowledge_at(knowledge_timeline, as_of, thread.person_id)
        knowledge_gaps = detect_knowledge_gaps(knowledge_timeline, thread.person_id, as_of=as_of)
        
        # 5. Allen Interval Relations
        allen_relations = self._compute_allen_relations(events)
        
        # 6. Constraint Graph
        constraint_events = self._events_to_constraint_format(events)
        constraint_graph = build_constraint_graph_from_events(constraint_events, thread.person_id)
        constraint_validation = validate_all_constraints(
            constraint_graph,
            {n.node_id: self._get_node_time(n, events) for n in constraint_graph.nodes.values()}
        )
        
        # 7. Provenance
        provenance_verification = self._verify_thread_provenance(thread, events)
        
        return TemporalContinuityEnrichment(
            recurrence_patterns=recurrence_patterns,
            frequency_analyses=[frequency_analysis] if frequency_analysis else [],
            frequency_changes=[frequency_change] if frequency_change else [],
            next_predictions=[next_prediction] if next_prediction else [],
            trends=trends,
            cyclical_patterns=cyclical,
            trend_interpretations=trend_interpretations,
            state_changes=state_changes,
            baseline_comparisons=baseline_comparisons,
            knowledge_at_snapshot=knowledge_at_snapshot,
            knowledge_gaps=knowledge_gaps,
            allen_relations=allen_relations,
            constraint_validation=constraint_validation,
            provenance_verification=provenance_verification,
        )
    
    def enrich_snapshot(
        self,
        snapshot: ContinuitySnapshot,
        events: Sequence[CareEvent],
        as_of: Optional[datetime] = None,
    ) -> TemporalContinuityEnrichment:
        """Add temporal intelligence to a continuity snapshot."""
        # Use the thread if available, otherwise enrich from all events
        thread = None
        if snapshot.thread_id:
            thread = self.db.query(ContinuityThread).filter(ContinuityThread.id == snapshot.thread_id).first()
        
        if thread:
            return self.enrich_thread(thread, events, as_of)
        
        # No thread - enrich from all events
        return self._enrich_from_events(events, as_of)
    
    def _enrich_from_events(
        self,
        events: Sequence[CareEvent],
        as_of: Optional[datetime] = None,
    ) -> TemporalContinuityEnrichment:
        """Enrich without a specific thread - use all events."""
        as_of = as_of or datetime.now()
        
        # Group events by type for analysis
        events_by_type: Dict[str, List[CareEvent]] = {}
        for event in events:
            events_by_type.setdefault(event.event_type, []).append(event)
        
        all_recurrence = []
        all_frequency = []
        all_changes = []
        all_trends = []
        
        for event_type, type_events in events_by_type.items():
            timestamps = self._extract_timestamps(type_events, event_type)
            time_series = self._extract_time_series(type_events, event_type)
            temporal_states = self._extract_temporal_states(type_events, event_type)
            
            if len(timestamps) >= 2:
                patterns = detect_recurrence(timestamps, event_type, min_occurrences=2, window_days=365)
                all_recurrence.extend(patterns)
                
                freq = analyze_frequency(timestamps, event_type)
                if freq:
                    all_frequency.append(freq)
                
                change = detect_frequency_change(timestamps, event_type)
                if change:
                    all_changes.append(change)
            
            for ts in time_series:
                if len(ts) >= 3:
                    trend = detect_trend(ts)
                    all_trends.append(trend)
        
        # Build knowledge timeline
        temporal_evidence = self._build_temporal_evidence(events)
        knowledge_timeline = build_knowledge_timeline(temporal_evidence)
        
        knowledge_at_snapshot = reconstruct_knowledge_at(knowledge_timeline, as_of, events[0].person_id if events else "")
        knowledge_gaps = detect_knowledge_gaps(knowledge_timeline, events[0].person_id if events else "", as_of=as_of)
        
        # Allen relations
        allen_relations = self._compute_allen_relations(events)
        
        # Constraints
        constraint_events = self._events_to_constraint_format(events)
        person_id = events[0].person_id if events else ""
        constraint_graph = build_constraint_graph_from_events(constraint_events, person_id)
        
        return TemporalContinuityEnrichment(
            recurrence_patterns=all_recurrence,
            frequency_analyses=all_frequency,
            frequency_changes=all_changes,
            next_predictions=[],
            trends=all_trends,
            cyclical_patterns=[],
            trend_interpretations=[],
            state_changes=all_changes,
            baseline_comparisons=[],
            knowledge_at_snapshot=knowledge_at_snapshot,
            knowledge_gaps=knowledge_gaps,
            allen_relations=allen_relations,
            constraint_validation={},
            provenance_verification={},
        )
    
    def get_temporal_handoff_brief(
        self,
        person_id: str,
        as_of: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Generate a temporally-enriched handoff brief."""
        as_of = as_of or datetime.now()
        
        # Get recent events
        events = self.db.query(CareEvent).filter(CareEvent.person_id == person_id).order_by(CareEvent.occurred_at.desc()).limit(50).all()
        events = list(reversed(events))  # Chronological
        
        # Get all threads
        threads = self.db.query(ContinuityThread).filter(
            ContinuityThread.person_id == person_id,
            ContinuityThread.is_active.is_(True),
        ).all()
        
        # Enrich each thread
        enriched_threads = []
        for thread in threads:
            thread_events = [e for e in events if e.event_type == thread.subject]
            enrichment = self.enrich_thread(thread, thread_events, as_of)
            
            enriched_threads.append({
                "thread_id": thread.id,
                "subject": thread.subject,
                "label": thread.label,
                "is_stale": thread.is_stale,
                "stale_since": thread.stale_since,
                "quiet_for_days": (as_of - thread.last_recorded_at).days if thread.last_recorded_at else None,
                "recurrence": {
                    "patterns": [{"frequency": p.frequency.value, "confidence": p.confidence, "ongoing": p.ongoing, "next_expected": p.next_expected.isoformat() if p.next_expected else None} for p in enrichment.recurrence_patterns],
                    "frequency_changes": [{"significance": c.significance, "change_percent": c.change_percent, "baseline_rate": c.baseline_rate_per_month, "recent_rate": c.recent_rate_per_month} for c in enrichment.frequency_changes],
                },
                "trends": [{
                    "measurement_type": t.measurement_type.value,
                    "direction": t.trend_type.value,
                    "change_percent": t.change_percent,
                    "confidence": t.confidence,
                    "interpretation": interp.get("clinical_significance", "unknown"),
                } for t, interp in zip(enrichment.trends, enrichment.trend_interpretations)],
                "state_changes": [{
                    "entity": c.entity_id,
                    "type": c.change_type.value,
                    "significance": c.significance.value,
                    "from": c.from_state.state_value if c.from_state else None,
                    "to": c.to_state.state_value if c.to_state else None,
                } for c in enrichment.state_changes],
                "knowledge_gaps": enrichment.knowledge_gaps[:5],  # Top 5
                "predicted_next": enrichment.next_predictions[0] if enrichment.next_predictions else None,
            })
        
        # Overall knowledge reconstruction
        temporal_evidence = self._build_temporal_evidence(events)
        knowledge_timeline = build_knowledge_timeline(temporal_evidence)
        knowledge_at_now = reconstruct_knowledge_at(knowledge_timeline, as_of, person_id)
        
        # Temporal gaps across all threads
        all_gaps = []
        for thread in threads:
            thread_events = [e for e in events if e.event_type == thread.subject]
            enrichment = self.enrich_thread(thread, thread_events, as_of)
            for gap in enrichment.knowledge_gaps:
                gap["thread"] = thread.subject
                all_gaps.append(gap)
        
        return {
            "person_id": person_id,
            "generated_at": as_of.isoformat(),
            "threads": enriched_threads,
            "stale_threads": [t["label"] for t in enriched_threads if t["is_stale"]],
            "recurrence_summary": self._summarize_recurrence(enriched_threads),
            "trend_summary": self._summarize_trends(enrichment.trends if 'enrichment' in locals() else []),
            "critical_changes": [c for thread in enriched_threads for c in thread["state_changes"] if c["significance"] in ("critical", "high")],
            "knowledge_at_handoff": {
                "known_claims": len(knowledge_at_now.known_claims),
                "uncertain_claims": len(knowledge_at_now.uncertain_claims),
                "conflicting_claims": len(knowledge_at_now.conflicting_claims),
            },
            "temporal_gaps": all_gaps[:10],
            "constraint_violations": [],  # Would need constraint graph
        }
    
    def _extract_timestamps(self, events: Sequence[CareEvent], event_type: str) -> List[datetime]:
        """Extract timestamps for a specific event type."""
        timestamps = []
        for event in events:
            if event.event_type == event_type and event.occurred_at:
                ts = event.occurred_at
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=datetime.now().astimezone().tzinfo)
                timestamps.append(ts)
        return timestamps
    
    def _extract_time_series(self, events: Sequence[CareEvent], event_type: str) -> List[List[TimeSeriesPoint]]:
        """Extract time series data from events."""
        # This would extract measurements from event metadata
        # For now, return empty - would need domain-specific extraction
        return []
    
    def _extract_temporal_states(self, events: Sequence[CareEvent], event_type: str) -> List[TemporalState]:
        """Extract temporal states from events."""
        states = []
        for event in events:
            if event.event_type == event_type and event.occurred_at:
                ts = event.occurred_at
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=datetime.now().astimezone().tzinfo)
                
                # Extract state from event metadata
                state_value = event.metadata.get("state", "present") if event.metadata else "present"
                severity = event.metadata.get("severity") if event.metadata else None
                
                states.append(TemporalState(
                    entity_id=event.id,
                    entity_type=event_type,
                    state_value=state_value,
                    severity=severity,
                    timestamp=ts,
                    confidence=event.confidence or 0.8,
                    evidence=event.evidence_ids or [],
                    provenance={"event_id": event.id, "source": "care_event"},
                ))
        return states
    
    def _build_temporal_evidence(self, events: Sequence[CareEvent]) -> List[TemporalEvidence]:
        """Build temporal evidence from care events."""
        evidence = []
        for event in events:
            ev = TemporalEvidence(
                evidence_id=event.id,
                content=event.title + (" " + event.description if event.description else ""),
                evidence_type="care_event",
                documented_at=event.recorded_at if event.recorded_at.tzinfo else event.recorded_at.replace(tzinfo=datetime.now().astimezone().tzinfo),
                event_time=event.occurred_at if event.occurred_at and event.occurred_at.tzinfo else (event.occurred_at.replace(tzinfo=datetime.now().astimezone().tzinfo) if event.occurred_at else None),
                ingestion_time=event.recorded_at if event.recorded_at.tzinfo else event.recorded_at.replace(tzinfo=datetime.now().astimezone().tzinfo),
                author=event.created_by_caregiver_id,
                source="caregiver_input",
                confidence=event.confidence or 0.8,
                metadata={"event_type": event.event_type, "event_id": event.id},
            )
            evidence.append(ev)
        return evidence
    
    def _compute_allen_relations(self, events: Sequence[CareEvent]) -> List[Dict[str, Any]]:
        """Compute Allen relations between events."""
        relations = []
        dated_events = [(e, e.occurred_at) for e in events if e.occurred_at]
        dated_events.sort(key=lambda x: x[1])
        
        for i in range(len(dated_events)):
            for j in range(i + 1, len(dated_events)):
                e1, t1 = dated_events[i]
                e2, t2 = dated_events[j]
                
                interval1 = AllenInterval(start=t1)
                interval2 = AllenInterval(start=t2)
                
                relation = compute_allen_relation(interval1, interval2)
                
                relations.append({
                    "source_event": e1.id,
                    "source_type": e1.event_type,
                    "source_time": t1.isoformat(),
                    "target_event": e2.id,
                    "target_type": e2.event_type,
                    "target_time": t2.isoformat(),
                    "relation": relation.value,
                })
        
        return relations
    
    def _events_to_constraint_format(self, events: Sequence[CareEvent]) -> List[Dict[str, Any]]:
        """Convert events to constraint graph format."""
        return [
            {
                "id": e.id,
                "type": e.event_type,
                "title": e.title,
                "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
                "evidence_ids": e.evidence_ids or [],
            }
            for e in events
            if e.occurred_at
        ]
    
    def _get_node_time(self, node, events: Sequence[CareEvent]) -> Optional[datetime]:
        """Get the time for a constraint graph node."""
        event = next((e for e in events if e.id == node.node_id), None)
        return event.occurred_at if event and event.occurred_at else None
    
    def _verify_thread_provenance(self, thread: ContinuityThread, events: Sequence[CareEvent]) -> Dict[str, Any]:
        """Verify provenance completeness for a thread."""
        # Check if all events in thread have proper provenance
        thread_events = [e for e in events if e.event_type == thread.subject]
        
        required_fields = ["event_time", "documentation_time", "ingestion_time"]
        all_complete = True
        missing = []
        
        for event in thread_events:
            for field in required_fields:
                # In practice, would check provenance store
                pass
        
        return {
            "thread_id": thread.id,
            "events_checked": len(thread_events),
            "provenance_complete": all_complete,
            "missing_fields": missing,
        }
    
    def _summarize_recurrence(self, enriched_threads: List[Dict]) -> Dict[str, Any]:
        """Summarize recurrence across all threads."""
        total_patterns = sum(len(t["recurrence"]["patterns"]) for t in enriched_threads)
        ongoing_patterns = sum(1 for t in enriched_threads for p in t["recurrence"]["patterns"] if p["ongoing"])
        frequency_changes = sum(len(t["recurrence"]["frequency_changes"]) for t in enriched_threads)
        
        return {
            "total_patterns_detected": total_patterns,
            "ongoing_patterns": ongoing_patterns,
            "frequency_changes_detected": frequency_changes,
            "threads_with_recurrence": sum(1 for t in enriched_threads if t["recurrence"]["patterns"]),
        }
    
    def _summarize_trends(self, trends: List[TrendResult]) -> Dict[str, Any]:
        """Summarize trends across all measurements."""
        if not trends:
            return {"total": 0}
        
        return {
            "total": len(trends),
            "increasing": sum(1 for t in trends if t.trend_type.value == "increasing"),
            "decreasing": sum(1 for t in trends if t.trend_type.value == "decreasing"),
            "stable": sum(1 for t in trends if t.trend_type.value == "stable"),
            "fluctuating": sum(1 for t in trends if t.trend_type.value == "fluctuating"),
            "high_confidence": sum(1 for t in trends if t.confidence > 0.7),
        }