"""Ensemble reasoning service that combines multiple reasoning approaches."""

# TEST_COMMENT_ADDED
from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.services.temporal_allen import TemporalInterval, AllenRelation
from app.services.causal_inference import CausalInferenceService
from app.services.belief_updater import BeliefUpdaterService
from app.services.proactive import ProactiveEngine
from app.models.identity import Person


class EnsembleReasoningService:
    """Service that combines multiple reasoning approaches for more robust conclusions."""
    
    def __init__(self, db: Session):
        self.db = db
        self.temporal_service = None  # Temporal reasoning is function-based, not a service
        self.causal_service = CausalInferenceService(db)
        self.belief_service = BeliefUpdaterService(db)
        self.proactive_service = ProactiveEngine(db)
    
    def temporal_reasoning_assessment(self, person_id: str, 
                                    time_intervals: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Perform temporal reasoning assessment using Allen's interval algebra.
        
        Args:
            person_id: ID of the person
            time_intervals: List of temporal intervals with start/end times and labels
            
        Returns:
            Dictionary containing temporal relationships and insights
        """
        # Convert input intervals to TemporalInterval objects
        intervals = []
        interval_labels = {}  # Store labels separately
        for i, interval_dict in enumerate(time_intervals):
            start_time = datetime.fromisoformat(interval_dict["start_time"]) \
                if isinstance(interval_dict["start_time"], str) else interval_dict["start_time"]
            end_time = datetime.fromisoformat(interval_dict["end_time"]) \
                if isinstance(interval_dict["end_time"], str) else interval_dict["end_time"]
            
            interval = TemporalInterval(
                start=start_time,
                end=end_time
            )
            intervals.append(interval)
            interval_labels[id(interval)] = interval_dict.get("label", f"interval_{i}")
        
        # Analyze temporal relationships between intervals
        relationships = []
        for i, interval1 in enumerate(intervals):
            for j, interval2 in enumerate(intervals[i+1:], i+1):
                relation = self._get_allen_relation(interval1, interval2)
                relationships.append({
                    "interval_i": interval_labels.get(id(interval1), f"interval_{i}"),
                    "interval_j": interval_labels.get(id(interval2), f"interval_{j}"),
                    "relation": relation.value,
                    "description": f"Interval {interval_labels.get(id(interval1), f'interval_{i}')} {relation.value} interval {interval_labels.get(id(interval2), f'interval_{j}')}"
                })
        
        # Detect temporal patterns
        patterns = self._detect_temporal_patterns(intervals)
        
        return {
            "person_id": person_id,
            "temporal_analysis": {
                "intervals_analyzed": len(intervals),
                "relationships": relationships,
                "patterns_detected": patterns,
                "temporal_consistency": self._assess_temporal_consistency(relationships)
            },
            "assessment_timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    def _get_allen_relation(self, interval1: TemporalInterval, interval2: TemporalInterval) -> AllenRelation:
        """Determine the Allen relation between two intervals."""
        # Simplified implementation - in reality, this would be more comprehensive
        if interval1.end < interval2.start:
            return AllenRelation.BEFORE
        elif interval1.start > interval2.end:
            return AllenRelation.AFTER
        elif interval1.end == interval2.start:
            return AllenRelation.MEETS
        elif interval2.end == interval1.start:
            return AllenRelation.MET_BY
        elif (interval1.start < interval2.start and interval1.end > interval2.end):
            return AllenRelation.CONTAINS
        elif (interval2.start < interval1.start and interval2.end > interval1.end):
            return AllenRelation.DURING
        elif (interval1.start <= interval2.start and interval1.end >= interval2.start and 
              interval1.end <= interval2.end):
            return AllenRelation.STARTS
        elif (interval2.start <= interval1.start and interval2.end >= interval1.start and 
              interval2.end <= interval1.end):
            return AllenRelation.STARTED_BY
        elif (interval1.start >= interval2.start and interval1.end <= interval2.end and
              interval1.end == interval2.end):
            return AllenRelation.FINISHES
        elif (interval2.start >= interval1.start and interval2.end <= interval1.end and
              interval2.end == interval1.end):
            return AllenRelation.FINISHED_BY
        elif (interval1.start == interval2.start and interval1.end == interval2.end):
            return AllenRelation.EQUALS
        else:
            return AllenRelation.UNKNOWN
    
    def _detect_temporal_patterns(self, intervals: List[TemporalInterval]) -> List[str]:
        """Detect temporal patterns in the intervals."""
        patterns = []
        
        # Check for regular intervals
        if len(intervals) >= 3:
            durations = [(interval.end - interval.start).total_seconds() for interval in intervals]
            if len(set(durations)) == 1:  # All durations equal
                patterns.append("regular_duration")
        
        # Check for frequent occurrences
        if len(intervals) > 5:
            patterns.append("high_frequency")
            
        return patterns
    
    def _assess_temporal_consistency(self, relationships: List[Dict[str, Any]]) -> str:
        """Assess the consistency of temporal relationships."""
        # Simple check for contradictions
        before_count = sum(1 for r in relationships if r["relation"] == "before")
        after_count = sum(1 for r in relationships if r["relation"] == "after")
        
        if before_count > 0 and after_count > 0:
            return "mixed"  # Potential inconsistency
        elif before_count > 0:
            return "sequential_forward"
        elif after_count > 0:
            return "sequential_backward"
        else:
            return "overlapping_or_equal"
    
    def causal_reasoning_assessment(self, person_id: str, 
                                  intervention: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform causal reasoning assessment.
        
        Args:
            person_id: ID of the person
            intervention: Dictionary specifying changes to nodes/edges
            
        Returns:
            Dictionary containing causal inference results
        """
        return self.causal_service.counterfactual_simulation(person_id, intervention)
    
    def belief_reasoning_assessment(self, person_id: str, 
                                  evidence_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Perform belief reasoning assessment.
        
        Args:
            person_id: ID of the person
            evidence_list: List of evidence dictionaries (not used in current implementation,
                          but kept for API consistency)
            
        Returns:
            Dictionary containing belief update results
        """
        # Note: The belief updater service uses all evidence for the person,
        # not just the evidence passed in. This is appropriate for an assessment
        # that wants to see the belief state given all available evidence.
        belief_state = self.belief_service.update_belief(person_id)
        
        # Convert belief state to dictionary for consistency
        if belief_state:
            return {
                "person_id": belief_state.person_id,
                "belief_state": belief_state.state_variables,
                "updated_at": belief_state.updated_at.isoformat() if belief_state.updated_at else None,
                "id": belief_state.id
            }
        else:
            return {}
    
    def active_reasoning_assessment(self, person_id: str) -> Dict[str, Any]:
        """
        Perform active reasoning assessment (evidence gap identification).
        
        Args:
            person_id: ID of the person
            
        Returns:
            Dictionary containing active reasoning results
        """
        return self.proactive_service.evaluate(person_id)
    
    def ensemble_assessment(self, person_id: str,
                          temporal_intervals: Optional[List[Dict[str, Any]]] = None,
                          intervention: Optional[Dict[str, Any]] = None,
                          evidence_list: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Perform an ensemble assessment combining multiple reasoning approaches.
        
        Args:
            person_id: ID of the person
            temporal_intervals: Optional temporal intervals for temporal reasoning
            intervention: Optional intervention for causal reasoning
            evidence_list: Optional evidence for belief reasoning
            
        Returns:
            Dictionary containing combined assessment from all reasoning approaches
        """
        # Initialize results
        temporal_result = {}
        causal_result = {}
        belief_result = {}
        active_result = {}
        
        # Perform temporal reasoning if intervals provided
        if temporal_intervals is not None:
            temporal_result = self.temporal_reasoning_assessment(person_id, temporal_intervals)
        
        # Perform causal reasoning if intervention provided
        if intervention is not None:
            causal_result = self.causal_reasoning_assessment(person_id, intervention)
        
        # Perform belief reasoning if evidence provided
        if evidence_list is not None:
            belief_result = self.belief_reasoning_assessment(person_id, evidence_list)
        
        # Perform active reasoning (always applicable)
        active_result = self.active_reasoning_assessment(person_id)
        
        # Combine insights from all reasoning approaches
        combined_insights = self._combine_insights(
            temporal_result, causal_result, belief_result, active_result
        )
        
        # Calculate ensemble confidence
        ensemble_confidence = self._calculate_ensemble_confidence(
            temporal_result, causal_result, belief_result, active_result
        )
        
        return {
            "person_id": person_id,
            "ensemble_assessment": {
                "temporal_reasoning": temporal_result,
                "causal_reasoning": causal_result,
                "belief_reasoning": belief_result,
                "active_reasoning": active_result,
                "combined_insights": combined_insights,
                "ensemble_confidence": ensemble_confidence
            },
            "assessment_timestamp": datetime.now(timezone.utc).isoformat(),
            "reasoning_modules_used": [
                module for module, result in [
                    ("temporal", temporal_result),
                    ("causal", causal_result),
                    ("belief", belief_result),
                    ("active", active_result)
                ] if result  # Only count non-empty results
            ]
        }
    
    def _combine_insights(self, temporal: Dict[str, Any], causal: Dict[str, Any],
                         belief: Dict[str, Any], active: Dict[str, Any]) -> List[str]:
        """Combine insights from all reasoning approaches."""
        insights = []
        
        # Extract insights from temporal reasoning
        if temporal.get("temporal_analysis"):
            patterns = temporal["temporal_analysis"].get("patterns_detected", [])
            if patterns:
                insights.append(f"Temporal patterns detected: {', '.join(patterns)}")
            
            consistency = temporal["temporal_analysis"].get("temporal_consistency", "")
            if consistency and consistency != "unknown":
                insights.append(f"Temporal consistency: {consistency}")
        
        # Extract insights from causal reasoning
        if causal.get("causal_inference_details"):
            method = causal["causal_inference_details"].get("method", "")
            if method:
                insights.append(f"Causal inference method: {method}")
        
        # Extract insights from belief reasoning
        if belief.get("belief_state"):
            insights.append("Belief state updated based on evidence")
        
        # Extract insights from active reasoning
        if active.get("evidence_gaps"):
            gaps = active["evidence_gaps"]
            if isinstance(gaps, list) and gaps:
                insights.append(f"Identified {len(gaps)} evidence gaps requiring attention")
            elif gaps and isinstance(gaps, dict) and gaps.get("total_gaps", 0) > 0:
                insights.append(f"Identified {gaps.get('total_gaps')} evidence gaps requiring attention")
        
        # Cross-reasoning insights
        if temporal and causal:
            insights.append("Temporal and causal analyses combined for dynamic intervention assessment")
        
        if belief and active:
            insights.append("Belief state and evidence gaps combined to prioritize evidence collection")
            
        return insights
    
    def _calculate_ensemble_confidence(self, temporal: Dict[str, Any], causal: Dict[str, Any],
                                     belief: Dict[str, Any], active: Dict[str, Any]) -> float:
        """Calculate confidence in the ensemble assessment."""
        # Start with base confidence
        confidence = 0.5
        
        # Boost confidence for each reasoning module that provided useful output
        if temporal and temporal.get("temporal_analysis"):
            confidence += 0.1
        if causal and causal.get("causal_inference_details"):
            confidence += 0.1
        if belief and belief.get("belief_state"):
            confidence += 0.1
        if active and active.get("evidence_gaps"):
            confidence += 0.1
        
        # Cap at 1.0
        return min(confidence, 1.0)


def get_ensemble_reasoning_service(db: Session) -> EnsembleReasoningService:
    """Dependency injection factory."""
    return EnsembleReasoningService(db)