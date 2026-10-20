from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.change_enums import (
    BaselineConfidence,
    ChangeType,
    SignificanceVerdict,
)
from app.core.prioritization_enums import (
    ActionabilityFactor,
    ContradictionType,
    PriorityDimension,
    PriorityLevel,
    PriorityReasonCategory,
    TrajectoryType,
    UncertaintyFactor,
    VulnerabilityFactor,
)
from app.core.uncertainty_enums import GapLifecycleStatus
from app.models.action import Action as ActionModel
from app.models.care_event import CareEvent
from app.models.caregiver import Caregiver
from app.models.evidence import Evidence
from app.models.caregiver_intelligence import CaregiverObservation
from app.models.change import Baseline, Change, Situation
from app.models.prioritization import (
    Prioritization,
    PrioritizationDimension,
    PrioritizationRanking,
)
from app.models.change import Situation as SituationModel
from app.models.time_location import (
    CareWindow as CareWindowModel,
    CareTransition as CareTransitionModel,
    Location as LocationModel,
    TimezoneContext as TimezoneContextModel,
)
from app.services.baseline import BaselineService
import logging
from app.services.change_detection import ChangeDetectionService
from app.services.situation_formation import SituationFormationService
from app.services.temporal_intelligence import TemporalIntelligenceService

prioritization_logger = logging.getLogger("prioritization")


class PrioritizationEngine:
    """
    Engine for calculating priority scores and recommendations.
    """

    def __init__(
        self,
        baseline_service: BaselineService = None,
        change_detection_service: ChangeDetectionService = None,
        situation_formation_service: SituationFormationService = None,
        temporal_intelligence_service: TemporalIntelligenceService = None,
    ):
        self.baseline_service = baseline_service or BaselineService()
        self.change_detection_service = change_detection_service or ChangeDetectionService()
        self.situation_formation_service = situation_formation_service or SituationFormationService()
        self.temporal_intelligence_service = temporal_intelligence_service or TemporalIntelligenceService()

    def detect(
        self,
        person_id: str,
        changes: List[Change],
        situations: List[Situation],
        reference_time: Optional[datetime] = None,
        db: Session = None
    ) -> List[Prioritization]:
        """
        Detect prioritizations based on changes and situations.
        
        Args:
            person_id: The person ID
            changes: List of changes
            situations: List of situations
            reference_time: Reference time for prioritization
            db: Database session
            
        Returns:
            List of Prioritization objects
        """
        # For now, return an empty list to avoid breaking the flow
        # TODO: Implement proper prioritization logic based on changes and situations
        return []

    def prioritize_events(
        self,
        person_id: str,
        events: List[CareEvent],
        db: Session,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Prioritization]:
        """
        Prioritize a list of care events for a person.
        
        Args:
            person_id: The ID of the person
            events: List of care events to prioritize
            db: Database session
            context: Optional context information
            
        Returns:
            List of Prioritization objects sorted by priority score (descending)
        """
        if not events:
            return []
        
        # Get baseline for the person
        baseline = self.baseline_service.get_or_create_baseline(person_id, db)
        
        # Detect changes from baseline
        changes = self.change_detection_service.detect_changes(baseline, events, db)
        
        # Form situations from events and changes
        situations = self.situation_formation_service.form_situations(events, changes, db)
        
        # Calculate priority scores for each situation
        prioritizations = []
        for situation in situations:
            prioritization = self._calculate_prioritization(situation, events, db, context)
            prioritizations.append(prioritization)
        
        # Sort by priority score (descending)
        prioritizations.sort(key=lambda p: p.score, reverse=True)
        
        return prioritizations

    def _calculate_prioritization(
        self,
        situation: SituationModel,
        events: List[CareEvent],
        db: Session,
        context: Optional[Dict[str, Any]] = None,
    ) -> Prioritization:
        """
        Calculate priority score for a situation.
        
        Args:
            situation: The situation to prioritize
            events: List of all care events for context
            db: Database session
            context: Optional context information
            
        Returns:
            Prioritization object with score and reasoning
        """
        # Initialize scoring components
        scores = {}
        reasoning = {}
        
        # 1. Actionability Factor
        actionability_score, actionability_reasoning = self._score_actionability(situation, events, db)
        scores[PriorityDimension.ACTIONABILITY] = actionability_score
        reasoning[PriorityDimension.ACTIONABILITY] = actionability_reasoning
        
        # 2. Contradiction Factor
        contradiction_score, contradiction_reasoning = self._score_contradiction(situation, events, db)
        scores[PriorityDimension.CONTRADICTION] = contradiction_score
        reasoning[PriorityDimension.CONTRADICTION] = contradiction_reasoning
        
        # 3. Priority Reason Category
        priority_reason_score, priority_reason_reasoning = self._score_priority_reason(situation, events, db)
        scores[PriorityDimension.PRIORITY_REASON] = priority_reason_score
        reasoning[PriorityDimension.PRIORITY_REASON] = priority_reason_reasoning
        
        # 4. Trajectory Type
        trajectory_score, trajectory_reasoning = self._score_trajectory(situation, events, db)
        scores[PriorityDimension.TRAJECTORY] = trajectory_score
        reasoning[PriorityDimension.TRAJECTORY] = trajectory_reasoning
        
        # 5. Uncertainty Factor
        uncertainty_score, uncertainty_reasoning = self._score_uncertainty(situation, events, db)
        scores[PriorityDimension.UNCERTAINTY] = uncertainty_score
        reasoning[PriorityDimension.UNCERTAINTY] = uncertainty_reasoning
        
        # 6. Vulnerability Factor
        vulnerability_score, vulnerability_reasoning = self._score_vulnerability(situation, events, db)
        scores[PriorityDimension.VULNERABILITY] = vulnerability_score
        reasoning[PriorityDimension.VULNERABILITY] = vulnerability_reasoning
        
        # Calculate weighted score
        weighted_score = 0.0
        total_weight = 0.0
        
        for dimension, score in scores.items():
            # Get weight from configuration (default to 1.0 if not specified)
            weight = self._get_dimension_weight(dimension)
            weighted_score += score * weight
            total_weight += weight
        
        if total_weight > 0:
            final_score = weighted_score / total_weight
        else:
            final_score = 0.0
        
        # Determine priority level based on score
        priority_level = self._score_to_priority_level(final_score)
        
        # Create and return prioritization object
        prioritization = Prioritization(
            id=str(uuid4()),
            situation_id=situation.id,
            score=final_score,
            priority_level=priority_level,
            reasoning=reasoning,
            created_at=datetime.now(timezone.utc),
        )
        
        return prioritization

    def _score_actionability(
        self,
        situation: SituationModel,
        events: List[CareEvent],
        db: Session,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Score the actionability of a situation.
        
        Args:
            situation: The situation to score
            events: List of all care events for context
            db: Database session
            
        Returns:
            Tuple of (score, reasoning_dict)
        """
        # Actionability is based on whether there are clear actions that can be taken
        # to address the situation
        
        # Check if situation has associated actions
        action_count = len(situation.actions) if hasattr(situation, 'actions') else 0
        
        # Base score on action count
        if action_count >= 3:
            score = 1.0
        elif action_count == 2:
            score = 0.7
        elif action_count == 1:
            score = 0.4
        else:
            score = 0.1  # Low actionability but not zero
        
        reasoning = {
            "action_count": action_count,
            "baseline_score": score,
        }
        
        return score, reasoning

    def _score_contradiction(
        self,
        situation: SituationModel,
        events: List[CareEvent],
        db: Session,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Score the contradiction level of a situation.
        
        Args:
            situation: The situation to score
            events: List of all care events for context
            db: Database session
            
        Returns:
            Tuple of (score, reasoning_dict)
        """
        # Contradiction scoring is inverted - lower contradiction = higher priority score
        # We calculate contradiction level and then invert it for scoring
        
        contradiction_level, contradiction_reasoning = self._calculate_contradiction_level(
            situation, events, db
        )
        
        # Invert contradiction level (0 = no contradiction -> score 1.0, 1 = max contradiction -> score 0.0)
        score = 1.0 - contradiction_level
        
        reasoning = {
            "contradiction_level": contradiction_level,
            "inverted_score": score,
            **contradiction_reasoning,
        }
        
        return score, reasoning

    def _calculate_contradiction_level(
        self,
        situation: SituationModel,
        events: List[CareEvent],
        db: Session,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Calculate the contradiction level of a situation.
        
        Args:
            situation: The situation to score
            events: List of all care events for context
            db: Database session
            
        Returns:
            Tuple of (contradiction_level, reasoning_dict)
        """
        # For now, we'll return a placeholder implementation
        # In a full implementation, this would analyze the situation for internal
        # inconsistencies or contradictions with known facts
        
        contradiction_level = 0.2  # Placeholder low contradiction
        reasoning = {
            "method": "placeholder",
            "details": "Contradiction detection not fully implemented",
        }
        
        return contradiction_level, reasoning

    def _score_priority_reason(
        self,
        situation: SituationModel,
        events: List[CareEvent],
        db: Session,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Score based on the priority reason category.
        
        Args:
            situation: The situation to score
            events: List of all care events for context
            db: Database session
            
        Returns:
            Tuple of (score, reasoning_dict)
        """
        # Priority reason category scoring
        # Different reason categories have different inherent priorities
        
        reason_scores = {
            PriorityReasonCategory.EMERGENCY: 1.0,
            PriorityReasonCategory.CRITICAL: 0.9,
            PriorityReasonCategory.WARNING: 0.7,
            PriorityReasonCategory.INFO: 0.4,
            PriorityReasonCategory.ROUTINE: 0.2,
        }
        
        reason = getattr(situation, 'priority_reason', None)
        score = reason_scores.get(reason, 0.5)  # Default to middle if unknown
        
        reasoning = {
            "priority_reason": reason.value if reason else None,
            "reason_score": score,
        }
        
        return score, reasoning

    def _score_trajectory(
        self,
        situation: SituationModel,
        events: List[CareEvent],
        db: Session,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Score based on the trajectory type.
        
        Args:
            situation: The situation to score
            events: List of all care events for context
            db: Database session
            
        Returns:
            Tuple of (score, reasoning_dict)
        """
        # Trajectory type scoring
        # Different trajectory types indicate different levels of concern
        
        trajectory_scores = {
            TrajectoryType.IMPROVING: 0.2,      # Improving situations are lower priority
            TrajectoryType.STABLE: 0.5,         # Stable situations are medium priority
            TrajectoryType.DECLINING: 0.8,      # Declining situations are high priority
            TrajectoryType.VOLATILE: 0.9,       # Volatile situations are very high priority
            TrajectoryType.UNKNOWN: 0.5,        # Unknown trajectory defaults to medium
        }
        
        trajectory = getattr(situation, 'trajectory', None)
        score = trajectory_scores.get(trajectory, 0.5)  # Default to middle if unknown
        
        reasoning = {
            "trajectory": trajectory.value if trajectory else None,
            "trajectory_score": score,
        }
        
        return score, reasoning

    def _score_uncertainty(
        self,
        situation: SituationModel,
        events: List[CareEvent],
        db: Session,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Score based on uncertainty factor.
        
        Args:
            situation: The situation to score
            events: List of all care events for context
            db: Database session
            
        Returns:
            Tuple of (score, reasoning_dict)
        """
        # Uncertainty scoring is inverted - higher uncertainty = lower priority score
        # We calculate certainty level and use that for scoring
        
        uncertainty_level, uncertainty_reasoning = self._calculate_uncertainty_level(
            situation, events, db
        )
        
        # Invert uncertainty level (0 = no uncertainty -> score 1.0, 1 = max uncertainty -> score 0.0)
        score = 1.0 - uncertainty_level
        
        reasoning = {
            "uncertainty_level": uncertainty_level,
            "inverted_score": score,
            **uncertainty_reasoning,
        }
        
        return score, reasoning

    def _calculate_uncertainty_level(
        self,
        situation: SituationModel,
        events: List[CareEvent],
        db: Session,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Calculate the uncertainty level of a situation.
        
        Args:
            situation: The situation to score
            events: List of all care events for context
            db: Database session
            
        Returns:
            Tuple of (uncertainty_level, reasoning_dict)
        """
        # For now, we'll return a placeholder implementation
        # In a full implementation, this would analyze data quality, missing
        # information, conflicting sources, etc.
        
        uncertainty_level = 0.3  # Placeholder medium-low uncertainty
        reasoning = {
            "method": "placeholder",
            "details": "Uncertainty calculation not fully implemented",
        }
        
        return uncertainty_level, reasoning

    def _score_vulnerability(
        self,
        situation: SituationModel,
        events: List[CareEvent],
        db: Session,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Score based on vulnerability factor.
        
        Args:
            situation: The situation to score
            events: List of all care events for context
            db: Database session
            
        Returns:
            Tuple of (score, reasoning_dict)
        """
        # Vulnerability scoring - higher vulnerability = higher priority score
        
        vulnerability_level, vulnerability_reasoning = self._calculate_vulnerability_level(
            situation, events, db
        )
        
        score = vulnerability_level  # Direct mapping: 0-1 vulnerability to 0-1 score
        
        reasoning = {
            "vulnerability_level": vulnerability_level,
            **vulnerability_reasoning,
        }
        
        return score, reasoning

    def _calculate_vulnerability_level(
        self,
        situation: SituationModel,
        events: List[CareEvent],
        db: Session,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Calculate the vulnerability level of a situation.
        
        Args:
            situation: The situation to score
            events: List of all care events for context
            db: Database session
            
        Returns:
            Tuple of (vulnerability_level, reasoning_dict)
        """
        # For now, we'll return a placeholder implementation
        # In a full implementation, this would consider factors like
        # age, existing conditions, support systems, etc.
        
        vulnerability_level = 0.4  # Placeholder medium vulnerability
        reasoning = {
            "method": "placeholder",
            "details": "Vulnerability calculation not fully implemented",
        }
        
        return vulnerability_level, reasoning

    def _get_dimension_weight(self, dimension: PriorityDimension) -> float:
        """
        Get the weight for a prioritization dimension.
        
        Args:
            dimension: The prioritization dimension
            
        Returns:
            Weight value (default 1.0)
        """
        # In a full implementation, these would be configurable
        # For now, we'll use equal weights
        weights = {
            PriorityDimension.ACTIONABILITY: 1.0,
            PriorityDimension.CONTRADICTION: 1.0,
            PriorityDimension.PRIORITY_REASON: 1.0,
            PriorityDimension.TRAJECTORY: 1.0,
            PriorityDimension.UNCERTAINTY: 1.0,
            PriorityDimension.VULNERABILITY: 1.0,
        }
        
        return weights.get(dimension, 1.0)

    def _score_to_priority_level(self, score: float) -> PriorityLevel:
        """
        Convert a numeric score to a priority level.
        
        Args:
            score: Numeric score (0.0 to 1.0)
            
        Returns:
            PriorityLevel enum value
        """
        if score >= 0.8:
            return PriorityLevel.CRITICAL
        elif score >= 0.6:
            return PriorityLevel.HIGH
        elif score >= 0.4:
            return PriorityLevel.MEDIUM
        elif score >= 0.2:
            return PriorityLevel.LOW
        else:
            return PriorityLevel.VERY_LOW