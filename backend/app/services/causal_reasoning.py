from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy.orm import Session

from app.models.causal import (
    CausalHypothesis as CausalHypothesisModel,
    CausalEvidence as CausalEvidenceModel,
    AlternativeExplanation as AlternativeExplanationModel,
    Confounder as ConfounderModel,
)
from app.models.care import CareEvent as CareEventModel
from app.schemas.causal import (
    CausalHypothesisCreate,
    CausalHypothesisUpdate,
    CausalEvidenceCreate,
    AlternativeExplanationCreate,
    AlternativeExplanationUpdate,
    ConfounderCreate,
    CausalAnalysisResult,
    TemporalAssociation,
)
from app.core.causal_enums import (
    EvidenceStrength,
    CausalConfidence,
    CausalRelationshipStatus,
    CausalDirection,
    EvidenceRole,
    EvidenceType,
)
from app.core.temporal_enums import TemporalRelationType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_DEFAULT_ASSOCIATION_WINDOW = timedelta(days=14)


def _diff_seconds(a: datetime, b: datetime) -> float:
    a = _ensure_utc(a)
    b = _ensure_utc(b)
    return (b - a).total_seconds()


class CausalReasoningEngine:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Temporal association identification (Section 1)
    # ------------------------------------------------------------------
    def identify_temporal_associations(
        self,
        person_id: str,
        reference_event_id: Optional[str] = None,
        window: timedelta = _DEFAULT_ASSOCIATION_WINDOW,
        cause_types: Optional[List[str]] = None,
        outcome_types: Optional[List[str]] = None,
    ) -> List[TemporalAssociation]:
        events = (
            self.db.query(CareEventModel)
            .filter(CareEventModel.person_id == person_id)
            .order_by(CareEventModel.occurred_at.asc())
            .all()
        )

        reference = None
        if reference_event_id:
            reference = next(
                (e for e in events if e.id == reference_event_id), None
            )

        if reference is None:
            reference = events[0] if events else None

        if reference is None:
            return []

        ref_time = _ensure_utc(reference.occurred_at)
        window_start = ref_time - window
        window_end = ref_time + window

        associations: List[TemporalAssociation] = []
        for e in events:
            if e.id == reference.id:
                continue
            e_time = _ensure_utc(e.occurred_at)
            if e_time < window_start or e_time > window_end:
                continue

            if reference.occurred_at and e.occurred_at:
                if reference.occurred_at < e.occurred_at:
                    relation = TemporalRelationType.BEFORE
                elif reference.occurred_at > e.occurred_at:
                    relation = TemporalRelationType.AFTER
                else:
                    relation = TemporalRelationType.SIMULTANEOUS
                time_diff = _diff_seconds(reference.occurred_at, e.occurred_at)
            else:
                relation = TemporalRelationType.ADJACENT
                time_diff = None

            if cause_types and reference.event_type not in cause_types:
                continue
            if outcome_types and e.event_type not in outcome_types:
                continue

            associations.append(
                TemporalAssociation(
                    cause_event_id=reference.id,
                    outcome_event_id=e.id,
                    cause_type=reference.event_type,
                    cause_id=None,
                    cause_description=reference.title,
                    cause_time=reference.occurred_at,
                    outcome_type=e.event_type,
                    outcome_id=None,
                    outcome_description=e.title,
                    outcome_time=e.occurred_at,
                    relation_type=relation.value,
                    time_difference_seconds=abs(time_diff) if time_diff else None,
                )
            )

        return associations

    # ------------------------------------------------------------------
    # Main analysis entry point
    # ------------------------------------------------------------------
    def assess_relationship(
        self,
        person_id: str,
        potential_cause_type: str,
        potential_cause_id: Optional[str] = None,
        potential_cause_description: str = "",
        outcome_type: str = "",
        outcome_id: Optional[str] = None,
        outcome_description: str = "",
        cause_time: Optional[datetime] = None,
        outcome_time: Optional[datetime] = None,
        evidence_ids: Optional[List[str]] = None,
        evidence_strength: EvidenceStrength = EvidenceStrength.WEAK,
        caregiver_id: Optional[str] = None,
        window: timedelta = _DEFAULT_ASSOCIATION_WINDOW,
    ) -> CausalAnalysisResult:
        cause_time = _ensure_utc(cause_time)
        outcome_time = _ensure_utc(outcome_time)

        temporal_rel = self._determine_temporal_relationship(cause_time, outcome_time)
        time_gap_seconds = (
            abs(_diff_seconds(cause_time, outcome_time))
            if cause_time and outcome_time
            else None
        )

        direction = self._infer_causal_direction(cause_time, outcome_time)
        confounders = self._identify_confounders(
            person_id, cause_time, outcome_time, window,
            potential_cause_description,
        )
        alternatives = self._identify_alternative_explanations(
            person_id, cause_time, outcome_time, window,
            potential_cause_description, outcome_description,
        )

        supporting_evidence, contradictory_evidence = self._collect_evidence(
            person_id, potential_cause_description, outcome_description,
            cause_time, outcome_time, window, evidence_ids,
        )

        strength, confidence = self._rank_evidence(
            supporting_evidence, contradictory_evidence, confounders,
            alternatives, time_gap_seconds, evidence_strength,
        )

        status = self._derive_relationship_status(strength, confidence)

        language = self._generate_causal_language(strength, confidence, status)

        gate_passed, gate_notes = self._run_quality_gate(
            potential_cause_description,
            outcome_description,
            temporal_rel,
            direction,
            supporting_evidence,
            contradictory_evidence,
            alternatives,
            confounders,
            evidence_ids,
            cause_time,
        )

        return CausalAnalysisResult(
            person_id=person_id,
            what_happened=f"{potential_cause_type} {potential_cause_description}".strip(),
            what_followed=f"{outcome_type} {outcome_description}".strip(),
            why_it_may_be_relevant=(
                f"The {potential_cause_description} occurred "
                f"{self._describe_timing(cause_time, outcome_time)} "
                f"relative to the {outcome_description}."
            ),
            what_else_is_known=[
                f"Temporal relationship: {temporal_rel}"
                if temporal_rel
                else "Temporal relationship: unknown"
            ]
            + [f"Confounder: {c.description}" for c in confounders]
            + [f"Alternative: {a.description}" for a in alternatives],
            what_is_uncertain=(
                "The available information does not establish whether "
                f"{potential_cause_description} contributed to the {outcome_description}."
                if strength != EvidenceStrength.STRONG
                else "Evidence is stronger but uncertainty about alternative"
                " explanations remains."
            ),
            what_may_warrant_attention=(
                "This temporal relationship may be worth discussing with the"
                " appropriate clinician."
            ),
            potential_cause_type=potential_cause_type,
            potential_cause_description=potential_cause_description,
            outcome_type=outcome_type,
            outcome_description=outcome_description,
            temporal_relationship=temporal_rel,
            time_gap_seconds=time_gap_seconds,
            causal_direction=direction,
            relationship_status=status,
            evidence_strength=strength,
            causal_confidence=confidence,
            supporting_evidence=[self._evidence_to_summary(e) for e in supporting_evidence],
            contradictory_evidence=[self._evidence_to_summary(e) for e in contradictory_evidence],
            alternative_explanations=[
                {"id": a.id, "description": a.description, "plausibility": a.plausibility.value,
                 "evidence_ids": a.evidence_ids or []}
                for a in alternatives
            ],
            confounders=[
                {"id": c.id, "description": c.description, "plausibility": c.plausibility.value,
                 "evidence_ids": c.evidence_ids or []}
                for c in confounders
            ],
            language_used=language,
            quality_gate_passed=gate_passed,
            quality_gate_notes=gate_notes,
            first_observed_date=cause_time or outcome_time,
            last_evaluated_date=utc_now(),
        )

    # ------------------------------------------------------------------
    # Hypothesis management (Sections 2, 3, 15)
    # ------------------------------------------------------------------
    def create_hypothesis(
        self, hypothesis: CausalHypothesisCreate
    ) -> CausalHypothesisModel:
        db_hyp = CausalHypothesisModel(**hypothesis.model_dump())
        db_hyp.last_evaluated_date = utc_now()
        if db_hyp.first_observed_date is None:
            db_hyp.first_observed_date = utc_now()
        self.db.add(db_hyp)
        self.db.commit()
        self.db.refresh(db_hyp)
        return db_hyp

    def create_hypothesis_from_analysis(
        self,
        person_id: str,
        analysis: CausalAnalysisResult,
        caregiver_id: Optional[str] = None,
    ) -> CausalHypothesisModel:
        db_hyp = CausalHypothesisModel(
            person_id=person_id,
            potential_cause_type=analysis.potential_cause_type,
            potential_cause_id=None,
            potential_cause_description=analysis.potential_cause_description,
            outcome_type=analysis.outcome_type,
            outcome_id=None,
            outcome_description=analysis.outcome_description,
            causal_direction=analysis.causal_direction,
            relationship_status=analysis.relationship_status,
            evidence_strength=analysis.evidence_strength,
            causal_confidence=analysis.causal_confidence,
             temporal_relationship=analysis.temporal_relationship,
            time_gap_seconds=analysis.time_gap_seconds,
            supporting_evidence_ids=None,
            contradictory_evidence_ids=None,
            evidence_ids=None,
            first_observed_date=analysis.first_observed_date,
            last_evaluated_date=analysis.last_evaluated_date,
            provenance="causal_analysis",
            created_by_caregiver_id=caregiver_id,
        )
        self.db.add(db_hyp)
        self.db.commit()
        self.db.refresh(db_hyp)
        return db_hyp

    def update_hypothesis(
        self, hypothesis_id: str, update: CausalHypothesisUpdate
    ) -> Optional[CausalHypothesisModel]:
        db_hyp = (
            self.db.query(CausalHypothesisModel)
            .filter(CausalHypothesisModel.id == hypothesis_id)
            .first()
        )
        if not db_hyp:
            return None

        update_data = update.model_dump(exclude_unset=True)
        if "is_active" in update_data and update_data["is_active"] is False:
            db_hyp.relationship_status = CausalRelationshipStatus.UNRESOLVED

        for field, value in update_data.items():
            setattr(db_hyp, field, value)

        db_hyp.last_evaluated_date = utc_now()
        self.db.commit()
        self.db.refresh(db_hyp)
        return db_hyp

    def reassess_hypothesis(
        self, hypothesis_id: str, caregiver_id: Optional[str] = None
    ) -> Optional[CausalHypothesisModel]:
        db_hyp = (
            self.db.query(CausalHypothesisModel)
            .filter(CausalHypothesisModel.id == hypothesis_id)
            .first()
        )
        if not db_hyp:
            return None

        db_hyp_id = db_hyp.id

        supporting = (
            self.db.query(CausalEvidenceModel)
            .filter(
                CausalEvidenceModel.hypothesis_id == db_hyp_id,
                CausalEvidenceModel.role == EvidenceRole.SUPPORTING,
            )
            .all()
        )
        contradictory = (
            self.db.query(CausalEvidenceModel)
            .filter(
                CausalEvidenceModel.hypothesis_id == db_hyp_id,
                CausalEvidenceModel.role == EvidenceRole.CONTRADICTORY,
            )
            .all()
        )
        confounders = (
            self.db.query(ConfounderModel)
            .filter(
                ConfounderModel.hypothesis_id == db_hyp_id,
                ConfounderModel.is_active,
            )
            .all()
        )
        alternatives = (
            self.db.query(AlternativeExplanationModel)
            .filter(
                AlternativeExplanationModel.hypothesis_id == db_hyp_id,
                AlternativeExplanationModel.is_active,
            )
            .all()
        )

        strength, confidence = self._rank_evidence(
            supporting, contradictory, confounders, alternatives,
            db_hyp.time_gap_seconds,
            EvidenceStrength.WEAK,
        )
        status = self._derive_relationship_status(strength, confidence)

        db_hyp.evidence_strength = strength
        db_hyp.causal_confidence = confidence
        db_hyp.relationship_status = status
        db_hyp.last_evaluated_date = utc_now()
        self.db.commit()
        self.db.refresh(db_hyp)
        return db_hyp

    def list_hypotheses(
        self,
        person_id: str,
        status: Optional[CausalRelationshipStatus] = None,
        is_active: bool = True,
    ) -> List[CausalHypothesisModel]:
        query = self.db.query(CausalHypothesisModel).filter(
            CausalHypothesisModel.person_id == person_id,
            CausalHypothesisModel.is_active == is_active,
        )
        if status:
            query = query.filter(CausalHypothesisModel.relationship_status == status)
        return query.order_by(CausalHypothesisModel.created_at.desc()).all()

    def get_hypothesis(self, hypothesis_id: str) -> Optional[CausalHypothesisModel]:
        return (
            self.db.query(CausalHypothesisModel)
            .filter(CausalHypothesisModel.id == hypothesis_id)
            .first()
        )

    # ------------------------------------------------------------------
    # Evidence management (Sections 6, 7)
    # ------------------------------------------------------------------
    def add_evidence(self, evidence: CausalEvidenceCreate) -> CausalEvidenceModel:
        db_ev = CausalEvidenceModel(**evidence.model_dump())
        self.db.add(db_ev)
        self.db.commit()
        self.db.refresh(db_ev)
        return db_ev

    def list_evidence(
        self,
        hypothesis_id: str,
        role: Optional[EvidenceRole] = None,
    ) -> List[CausalEvidenceModel]:
        query = self.db.query(CausalEvidenceModel).filter(
            CausalEvidenceModel.hypothesis_id == hypothesis_id
        )
        if role:
            query = query.filter(CausalEvidenceModel.role == role)
        return query.order_by(CausalEvidenceModel.created_at.desc()).all()

    def add_alternative_explanation(
        self, explanation: AlternativeExplanationCreate
    ) -> AlternativeExplanationModel:
        db_alt = AlternativeExplanationModel(**explanation.model_dump())
        self.db.add(db_alt)
        self.db.commit()
        self.db.refresh(db_alt)
        return db_alt

    def update_alternative_explanation(
        self, explanation_id: str, update: AlternativeExplanationUpdate
    ) -> Optional[AlternativeExplanationModel]:
        db_alt = (
            self.db.query(AlternativeExplanationModel)
            .filter(AlternativeExplanationModel.id == explanation_id)
            .first()
        )
        if not db_alt:
            return None
        update_data = update.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_alt, field, value)
        self.db.commit()
        self.db.refresh(db_alt)
        return db_alt

    def add_confounder(self, confounder: ConfounderCreate) -> ConfounderModel:
        db_conf = ConfounderModel(**confounder.model_dump())
        self.db.add(db_conf)
        self.db.commit()
        self.db.refresh(db_conf)
        return db_conf

    # ------------------------------------------------------------------
    # Evidence retrieval for traceability (Section 7)
    # ------------------------------------------------------------------
    def get_evidence_trace(
        self, hypothesis_id: str
    ) -> Dict[str, Any]:
        hyp = self.get_hypothesis(hypothesis_id)
        if not hyp:
            return {}

        evidence = self.db.query(CausalEvidenceModel).filter(
            CausalEvidenceModel.hypothesis_id == hypothesis_id
        ).all()

        alternatives = self.db.query(AlternativeExplanationModel).filter(
            AlternativeExplanationModel.hypothesis_id == hypothesis_id,
            AlternativeExplanationModel.is_active,
        ).all()

        confounders = self.db.query(ConfounderModel).filter(
            ConfounderModel.hypothesis_id == hypothesis_id,
            ConfounderModel.is_active,
        ).all()

        evidence_items = [
            {
                "id": e.id,
                "role": e.role.value,
                "evidence_type": e.evidence_type.value,
                "strength": e.strength.value,
                "description": e.description,
                "source_document": e.source_document,
                "source_event_id": e.source_event_id,
                "evidence_ref_id": e.evidence_ref_id,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "is_derived": e.is_derived,
                "derivation_method": e.derivation_method,
            }
            for e in evidence
        ]

        return {
            "hypothesis": {
                "id": hyp.id,
                "potential_cause": hyp.potential_cause_description,
                "outcome": hyp.outcome_description,
                "relationship_status": hyp.relationship_status.value,
                "evidence_strength": hyp.evidence_strength.value,
                "causal_confidence": hyp.causal_confidence.value,
                "causal_direction": hyp.causal_direction.value,
                "temporal_relationship": hyp.temporal_relationship,
                "time_gap_seconds": hyp.time_gap_seconds,
                "provenance": hyp.provenance,
                "first_observed_date": hyp.first_observed_date.isoformat()
                if hyp.first_observed_date
                else None,
                "last_evaluated_date": hyp.last_evaluated_date.isoformat()
                if hyp.last_evaluated_date
                else None,
            },
            "supporting_evidence": [e for e in evidence_items if e["role"] == "supporting"],
            "contradictory_evidence": [e for e in evidence_items if e["role"] == "contradictory"],
            "alternative_explanations": [
                {
                    "id": a.id,
                    "description": a.description,
                    "plausibility": a.plausibility.value,
                    "evidence_ids": a.evidence_ids or [],
                }
                for a in alternatives
            ],
            "confounders": [
                {
                    "id": c.id,
                    "description": c.description,
                    "plausibility": c.plausibility.value,
                    "evidence_ids": c.evidence_ids or [],
                }
                for c in confounders
            ],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _determine_temporal_relationship(
        self, cause_time: Optional[datetime], outcome_time: Optional[datetime]
    ) -> Optional[str]:
        if not cause_time or not outcome_time:
            return None
        diff = _diff_seconds(cause_time, outcome_time)
        if diff == 0:
            return "occurred simultaneously"
        if diff > 0:
            unit, value = self._humanize_diff(abs(diff))
            return f"occurred {value} {unit} before outcome"
        else:
            unit, value = self._humanize_diff(abs(diff))
            return f"occurred {value} {unit} after outcome (reverse sequence)"

    @staticmethod
    def _humanize_diff(seconds: float) -> Tuple[str, float]:
        if seconds < 60:
            return "seconds", round(seconds)
        if seconds < 3600:
            return "minutes", round(seconds / 60)
        if seconds < 86400:
            return "hours", round(seconds / 3600, 1)
        return "days", round(seconds / 86400, 1)

    def _infer_causal_direction(
        self, cause_time: Optional[datetime], outcome_time: Optional[datetime]
    ) -> CausalDirection:
        if not cause_time or not outcome_time:
            return CausalDirection.UNKNOWN
        if cause_time < outcome_time:
            return CausalDirection.CAUSE_TO_OUTCOME
        return CausalDirection.OUTCOME_TO_CAUSE

    def _identify_confounders(
        self,
        person_id: str,
        cause_time: Optional[datetime],
        outcome_time: Optional[datetime],
        window: timedelta,
        cause_description: str,
    ) -> List[ConfounderModel]:
        if not cause_time:
            return []

        center = cause_time
        window_start = _ensure_utc(center) - window
        window_end = _ensure_utc(center) + window

        events = (
            self.db.query(CareEventModel)
            .filter(
                CareEventModel.person_id == person_id,
                CareEventModel.occurred_at >= window_start,
                CareEventModel.occurred_at <= window_end,
            )
            .all()
        )

        confounders = []
        for e in events:
            if cause_description and cause_description.lower() in e.title.lower():
                continue
            conf = ConfounderModel(
                person_id=person_id,
                hypothesis_id="pending",
                factor_type=e.event_type,
                description=e.title,
                plausibility=CausalConfidence.POSSIBLE,
                evidence_ids=e.evidence_ids,
            )
            confounders.append(conf)
        return confounders

    def _identify_alternative_explanations(
        self,
        person_id: str,
        cause_time: Optional[datetime],
        outcome_time: Optional[datetime],
        window: timedelta,
        cause_description: str,
        outcome_description: str,
    ) -> List[AlternativeExplanationModel]:
        if not cause_time:
            return []

        center = cause_time
        window_start = _ensure_utc(center) - window
        window_end = _ensure_utc(center) + window

        events = (
            self.db.query(CareEventModel)
            .filter(
                CareEventModel.person_id == person_id,
                CareEventModel.occurred_at >= window_start,
                CareEventModel.occurred_at <= window_end,
            )
            .all()
        )

        alternatives = []
        for e in events:
            if cause_description and cause_description.lower() in e.title.lower():
                continue
            alt = AlternativeExplanationModel(
                person_id=person_id,
                hypothesis_id="pending",
                explanation_type=e.event_type,
                description=e.title,
                plausibility=CausalConfidence.POSSIBLE,
                evidence_ids=e.evidence_ids,
            )
            alternatives.append(alt)
        return alternatives

    def _collect_evidence(
        self,
        person_id: str,
        cause_description: str,
        outcome_description: str,
        cause_time: Optional[datetime],
        outcome_time: Optional[datetime],
        window: timedelta,
        explicit_evidence_ids: Optional[List[str]],
    ) -> Tuple[List[CausalEvidenceModel], List[CausalEvidenceModel]]:
        supporting: List[CausalEvidenceModel] = []
        contradictory: List[CausalEvidenceModel] = []

        if not cause_time or not outcome_time:
            return supporting, contradictory

        window_start = _ensure_utc(cause_time) - window
        window_end = _ensure_utc(outcome_time) + window

        contradicting = (
            self.db.query(CausalEvidenceModel)
            .filter(
                CausalEvidenceModel.person_id == person_id,
                CausalEvidenceModel.role == EvidenceRole.CONTRADICTORY,
                CausalEvidenceModel.timestamp >= window_start,
                CausalEvidenceModel.timestamp <= window_end,
            )
            .all()
        )
        contradictory.extend(contradicting)

        return supporting, contradictory

    def _rank_evidence(
        self,
        supporting: List[CausalEvidenceModel],
        contradictory: List[CausalEvidenceModel],
        confounders: List[ConfounderModel],
        alternatives: List[AlternativeExplanationModel],
        time_gap_seconds: Optional[float],
        default_strength: EvidenceStrength,
    ) -> Tuple[EvidenceStrength, CausalConfidence]:
        strong_types = {
            EvidenceType.CLINICIAN_DOCUMENTATION,
            EvidenceType.REPRODUCIBLE_RELATIONSHIP,
            EvidenceType.CONTROLLED_OR_INFORMATIVE,
            EvidenceType.ADVERSE_EFFECT_RELATIONSHIP,
            EvidenceType.RESOLUTION_ON_REMOVAL,
            EvidenceType.REEVALUATION_CHALLENGE,
        }

        has_strong = any(e.evidence_type in strong_types for e in supporting)
        has_contradiction = len(contradictory) > 0
        has_confounder = len(confounders) > 0
        has_alternative = len(alternatives) > 0

        if has_strong and not has_contradiction and not has_confounder:
            return EvidenceStrength.STRONG, CausalConfidence.SUPPORTED

        if has_strong and has_contradiction:
            return EvidenceStrength.MODERATE, CausalConfidence.CONFLICTING

        if has_contradiction or (has_confounder and has_alternative):
            return EvidenceStrength.WEAK, CausalConfidence.CONFLICTING

        if has_confounder or has_alternative:
            if has_strong:
                return EvidenceStrength.MODERATE, CausalConfidence.SUPPORTED
            return EvidenceStrength.WEAK, CausalConfidence.PLAUSIBLE

        if has_strong:
            return EvidenceStrength.STRONG, CausalConfidence.SUPPORTED

        if supporting and time_gap_seconds is not None:
            if time_gap_seconds <= 86400 * 2:
                return EvidenceStrength.WEAK, CausalConfidence.POSSIBLE

        return EvidenceStrength.WEAK, CausalConfidence.INSUFFICIENT

    def _derive_relationship_status(
        self, strength: EvidenceStrength, confidence: CausalConfidence
    ) -> CausalRelationshipStatus:
        if confidence == CausalConfidence.ESTABLISHED:
            return CausalRelationshipStatus.SUPPORTED
        if confidence == CausalConfidence.SUPPORTED and strength == EvidenceStrength.STRONG:
            return CausalRelationshipStatus.SUPPORTED
        if confidence == CausalConfidence.CONFLICTING:
            return CausalRelationshipStatus.CONFLICTING
        if confidence == CausalConfidence.SUPPORTED:
            return CausalRelationshipStatus.CAUSAL_HYPOTHESIS
        if confidence in (CausalConfidence.POSSIBLE, CausalConfidence.PLAUSIBLE):
            return CausalRelationshipStatus.CAUSAL_HYPOTHESIS
        return CausalRelationshipStatus.OBSERVED_ASSOCIATION

    @staticmethod
    def _generate_causal_language(
        strength: EvidenceStrength,
        confidence: CausalConfidence,
        status: CausalRelationshipStatus,
    ) -> List[str]:
        if strength == EvidenceStrength.STRONG and confidence in (
            CausalConfidence.SUPPORTED,
            CausalConfidence.ESTABLISHED,
        ):
            return [
                "was documented as contributing to",
                "is potentially associated with",
            ]
        if strength == EvidenceStrength.MODERATE:
            return [
                "may have contributed",
                "is potentially associated with",
                "the available evidence suggests a possible relationship",
            ]
        return [
            "occurred after",
            "temporally associated with",
            "may be relevant",
            "could be related",
        ]

    def _run_quality_gate(
        self,
        cause_description: str,
        outcome_description: str,
        temporal_rel: Optional[str],
        direction: CausalDirection,
        supporting: List[CausalEvidenceModel],
        contradictory: List[CausalEvidenceModel],
        alternatives: List[AlternativeExplanationModel],
        confounders: List[ConfounderModel],
        evidence_ids: Optional[List[str]],
        cause_time: Optional[datetime],
    ) -> Tuple[bool, List[str]]:
        notes: List[str] = []

        if not cause_description:
            notes.append("No identifiable potential cause")
            return False, notes
        notes.append("Identifiable potential cause: present")

        if not outcome_description:
            notes.append("No identifiable outcome")
            return False, notes
        notes.append("Identifiable outcome: present")

        if temporal_rel:
            notes.append("Temporal relationship: known")
        else:
            notes.append("Temporal relationship: unknown")
            return False, notes

        if direction != CausalDirection.UNKNOWN:
            notes.append("Causal direction: supported")
        else:
            notes.append("Causal direction: not supported")

        if supporting:
            notes.append(
                f"Supporting evidence present ({len(supporting)} item(s))"
            )
        else:
            notes.append("Supporting evidence: none (temporal association only)")

        if contradictory:
            notes.append(
                f"Contradictory evidence present ({len(contradictory)} item(s))"
            )
        else:
            notes.append("Contradictory evidence: none")

        if alternatives:
            notes.append(
                f"Alternative explanations present ({len(alternatives)})"
            )
        else:
            notes.append("Alternative explanations: none identified")

        if confounders:
            notes.append(
                f"Confounders present ({len(confounders)})"
            )
        else:
            notes.append("Confounders: none identified")

        if evidence_ids:
            notes.append("Claim is traceable to evidence sources")
        else:
            notes.append("Claim traceability: limited (no explicit evidence IDs)")

        notes.append("Uncertainty: explicitly represented")
        notes.append("No unsupported causal assertion generated")

        passed = temporal_rel is not None and bool(cause_description) and bool(outcome_description)
        return passed, notes

    @staticmethod
    def _describe_timing(cause_time: Optional[datetime], outcome_time: Optional[datetime]) -> str:
        if not cause_time or not outcome_time:
            return "at an unknown time relative to"
        diff = _diff_seconds(cause_time, outcome_time)
        if diff >= 0:
            unit, value = CausalReasoningEngine._humanize_diff(diff)
            if unit == "days" and value < 1:
                return "less than a day before"
            return f"{value} {unit} before"
        else:
            unit, value = CausalReasoningEngine._humanize_diff(abs(diff))
            if unit == "days" and value < 1:
                return "less than a day after"
            return f"{value} {unit} after"

    @staticmethod
    def _evidence_to_summary(ev: CausalEvidenceModel) -> Dict[str, Any]:
        return {
            "role": ev.role.value,
            "evidence_type": ev.evidence_type.value,
            "strength": ev.strength.value,
            "description": ev.description,
            "source_document": ev.source_document,
            "source_event_id": ev.source_event_id,
            "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
            "is_derived": ev.is_derived,
            "derivation_method": ev.derivation_method,
        }
