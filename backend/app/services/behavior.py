"""Why a behaviour happened.

This is the hardest question in the system and the one where a confident wrong
answer does the most damage. A caregiver who is told the reason and acts on it
will not look for the real reason.

The design refuses the single-cause answer structurally. An interpretation with
fewer than two hypotheses is recorded as incomplete, and the flag is carried on
the row, because "the medication" is almost never the whole of it and a reading
that presents one cause as the cause is worse than no reading at all.

Three further rules are enforced rather than requested:

No hypothesis may be promoted to "supported" on one piece of evidence. The
ranking is advisory; the strength is derived from the evidence attached to it.

The observation is stored before the interpretation, separately, so the
behaviour survives every theory later attached to it. Trust rule 3: what the
caregiver saw is never overwritten by our language about it.

Every hypothesis is its own row, so a caregiver who knows this is not what
happened can reject that one hypothesis without discarding the rest.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.care_context_enums import BehaviorCategory, HypothesisStrength
from app.core.llm_enums import LLMReasoningMode, LLMTask
from app.models.behavior import (
    BehaviorHypothesis,
    BehaviorInterpretation,
    BehaviorObservation,
)
from app.models.llm import LLMReasoningResult
from app.services.care_common import (
    evidence_map,
    ensure_utc,
    parse_time,
    unique,
    utc_now,
)
from app.services.llm import LocalReasoningEngine

# Fewer than this many and the interpretation is marked incomplete rather than
# presented as an answer.
_MIN_HYPOTHESES = 2
# A single piece of evidence cannot make a hypothesis "supported".
_SUPPORTED_MIN_EVIDENCE = 2

# The explanation space the record can actually support. Not a diagnosis list;
# things worth ruling in or out before anything more specific is considered.
_CONSIDERATIONS = (
    "pain",
    "sleep",
    "medication effect or omission",
    "infection",
    "constipation",
    "hunger",
    "thirst",
    "over-stimulation",
    "a change of person",
    "a change of routine",
    "unmet need",
    "a need to communicate",
    "fear",
    "a pattern specific to this person",
)

_SAFETY_CATEGORIES = {BehaviorCategory.ELOPEMENT, BehaviorCategory.PAIN_DISTRESS}


class BehaviorEngine:
    def __init__(self, db: Session, engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self.engine = engine or LocalReasoningEngine(db)

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def record_observation(
        self,
        person_id: str,
        description: str,
        category: Any = BehaviorCategory.OTHER,
        original_text: Optional[str] = None,
        occurred_at: Optional[Any] = None,
        time_of_day: Optional[str] = None,
        location: Optional[str] = None,
        observed_by_caregiver_id: Optional[str] = None,
        preceding_context: Optional[str] = None,
        response_taken: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        is_safety_concern: Optional[bool] = None,
        flush: bool = True,
    ) -> BehaviorObservation:
        """Record what happened, with no theory attached.

        original_text is kept distinct from description so the caregiver's own
        words are never replaced by ours, whatever interpretation follows.
        """
        if not description or not description.strip():
            raise ValueError("A behaviour observation needs a description")
        category_value = _enum_or_value(category, BehaviorCategory)
        observation = BehaviorObservation(
            person_id=person_id,
            category=category_value,
            description=description.strip(),
            original_text=(original_text or "").strip() or None,
            occurred_at=ensure_utc(parse_time(occurred_at)),
            time_of_day=time_of_day,
            location=location,
            observed_by_caregiver_id=observed_by_caregiver_id,
            preceding_context=preceding_context,
            response_taken=response_taken,
            duration_minutes=duration_minutes,
            evidence_ids=list(evidence_ids) if evidence_ids else None,
            is_safety_concern=(
                is_safety_concern
                if is_safety_concern is not None
                else category_value in _SAFETY_CATEGORIES
            ),
        )
        self.db.add(observation)
        if flush:
            self.db.flush()
        return observation

    # ------------------------------------------------------------------
    # Interpretation
    # ------------------------------------------------------------------

    def interpret(
        self, observation_id: str, person_id: Optional[str] = None
    ) -> BehaviorInterpretation:
        """Offer ranked, competing explanations. Never a single cause."""
        observation = (
            self.db.query(BehaviorObservation)
            .filter(BehaviorObservation.id == observation_id)
            .first()
        )
        if observation is None:
            raise ValueError("Behavior observation not found")
        person_id = person_id or observation.person_id

        prior_observations = (
            self.db.query(BehaviorObservation)
            .filter(
                BehaviorObservation.person_id == person_id,
                BehaviorObservation.id != observation.id,
            )
            .order_by(BehaviorObservation.occurred_at.desc().nullslast())
            .limit(20)
            .all()
        )
        evidence_ids = unique(
            list(observation.evidence_ids or [])
            + [eid for o in prior_observations for eid in (o.evidence_ids or [])]
        )
        texts = evidence_map(self.db, evidence_ids)

        from app.services.person_model import PersonModelEngine

        person_model = PersonModelEngine(self.db)
        known_triggers = person_model.trigger_view(person_id)
        known_calmers = person_model.calmer_view(person_id)

        fallback = _deterministic_interpretation(observation, prior_observations, known_triggers)
        result: Optional[LLMReasoningResult] = None
        hypotheses: List[Dict[str, Any]] = []
        settle: List[Dict[str, Any]] = []
        pattern_match: Optional[str] = None
        summary = fallback["summary"]
        possible_context = fallback["possible_context"]
        confidence: Optional[float] = None
        result_id: Optional[str] = None

        model_result = self.engine.reason(
            task=LLMTask.BEHAVIOR_INTERPRETATION,
            person_id=person_id,
            subject_type="behavior_observation",
            subject_id=observation.id,
            payload={
                "observed_behaviour": {
                    "description": observation.description,
                    "original_text": observation.original_text,
                    "category": _enum_value(observation.category),
                    "occurred_at": ensure_utc(observation.occurred_at).isoformat()
                    if ensure_utc(observation.occurred_at)
                    else None,
                    "time_of_day": observation.time_of_day,
                    "location": observation.location,
                    "preceding_context": observation.preceding_context,
                    "response_taken": observation.response_taken,
                    "duration_minutes": observation.duration_minutes,
                },
                "worth_considering": list(_CONSIDERATIONS),
                "already_known_about_this_person": {
                    "triggers": known_triggers,
                    "calming_measures": known_calmers,
                },
                "previous_behaviours": [
                    {
                        "description": o.description,
                        "category": _enum_value(o.category),
                        "occurred_at": ensure_utc(o.occurred_at).isoformat()
                        if ensure_utc(o.occurred_at)
                        else None,
                        "time_of_day": o.time_of_day,
                    }
                    for o in prior_observations[:10]
                ],
                "evidence": texts,
            },
            evidence_ids=evidence_ids,
            evidence_texts=texts,
            mode=LLMReasoningMode.LLM_ASSISTED,
        )

        if model_result is not None:
            result = model_result
            detail = _detail_map(result)
            hypotheses = [h for h in (detail.get("hypotheses") or []) if isinstance(h, dict)]
            settle = [s for s in (detail.get("what_would_settle_it") or []) if isinstance(s, dict)]
            pattern_match = detail.get("pattern_match")
            summary = result.summary
            possible_context = result.possible_context or possible_context
            confidence = result.confidence
            result_id = result.id
        else:
            hypotheses = fallback["hypotheses"]
            settle = fallback["what_would_settle_it"]

        hypotheses = _normalise_hypotheses(hypotheses, evidence_ids)
        count = len(hypotheses)
        complete = count >= _MIN_HYPOTHESES
        if not complete:
            summary = (
                f"{summary} This is a single possible explanation, not an answer. "
                "It should not be acted on without checking at least one other possibility."
            )
            possible_context = (
                f"Only one explanation was available. The record does not support a second one yet, and "
                f"one explanation is rarely the whole of it. Worth recording more of what happens around "
                f"this: {_join(_CONSIDERATIONS[:5])}."
            )

        existing = (
            self.db.query(BehaviorInterpretation)
            .filter(
                BehaviorInterpretation.observation_id == observation.id,
                BehaviorInterpretation.is_active.is_(True),
            )
            .order_by(BehaviorInterpretation.created_at.desc())
            .first()
        )

        interpretation = BehaviorInterpretation(
            person_id=person_id,
            observation_id=observation.id,
            hypotheses=hypotheses,
            hypothesis_count=count,
            is_complete=complete,
            what_would_settle_it=settle,
            pattern_match=pattern_match,
            summary=summary,
            evidence_ids=unique(list(result.evidence_ids or []) if result else []) or evidence_ids,
            possible_context=possible_context,
            confidence=confidence,
            requires_human_review=True,
            reasoning_result_id=result_id,
        )
        self.db.add(interpretation)
        self.db.flush()

        for index, hypothesis in enumerate(hypotheses):
            self.db.add(
                BehaviorHypothesis(
                    interpretation_id=interpretation.id,
                    person_id=person_id,
                    rank=index + 1,
                    explanation=hypothesis["explanation"],
                    strength=hypothesis["strength"],
                    supporting_evidence_ids=hypothesis["supporting_evidence_ids"],
                    contradicting_evidence_ids=hypothesis["contradicting_evidence_ids"],
                    why_plausible=hypothesis["why_plausible"],
                )
            )

        if existing is not None:
            existing.is_active = False
            existing.superseded_by_interpretation_id = interpretation.id
        self.db.flush()
        return interpretation

    def list_observations(
        self, person_id: str, category: Optional[Any] = None, limit: int = 50
    ) -> List[BehaviorObservation]:
        query = self.db.query(BehaviorObservation).filter(
            BehaviorObservation.person_id == person_id
        )
        if category is not None:
            query = query.filter(BehaviorObservation.category == _enum_or_value(category, BehaviorCategory))
        return query.order_by(BehaviorObservation.occurred_at.desc().nullslast()).all()[:limit]

    def get_interpretation(
        self, observation_id: str
    ) -> Optional[BehaviorInterpretation]:
        return (
            self.db.query(BehaviorInterpretation)
            .filter(
                BehaviorInterpretation.observation_id == observation_id,
                BehaviorInterpretation.is_active.is_(True),
            )
            .order_by(BehaviorInterpretation.created_at.desc())
            .first()
        )

    def list_hypotheses(self, interpretation_id: str) -> List[BehaviorHypothesis]:
        return (
            self.db.query(BehaviorHypothesis)
            .filter(BehaviorHypothesis.interpretation_id == interpretation_id)
            .order_by(BehaviorHypothesis.rank.asc())
            .all()
        )

    def reject_hypothesis(
        self,
        hypothesis_id: str,
        reason: str,
        caregiver_id: Optional[str] = None,
    ) -> BehaviorHypothesis:
        """A caregiver says this one is not what happened.

        Logged rather than deleted. The next interpretation of this behaviour
        should not re-offer a hypothesis the person who was there has already
        ruled out, and that is only possible if the rejection is remembered.
        """
        hypothesis = (
            self.db.query(BehaviorHypothesis)
            .filter(BehaviorHypothesis.id == hypothesis_id)
            .first()
        )
        if hypothesis is None:
            raise ValueError("Behavior hypothesis not found")
        hypothesis.is_active = False
        hypothesis.rejected_reason = reason
        hypothesis.rejected_by_caregiver_id = caregiver_id
        hypothesis.rejected_at = utc_now()
        self.db.flush()
        return hypothesis

    def behaviour_view(self, person_id: str) -> Dict[str, Any]:
        observations = self.list_observations(person_id)
        rows = []
        incomplete = 0
        for observation in observations:
            interpretation = self.get_interpretation(observation.id)
            if interpretation is not None and not interpretation.is_complete:
                incomplete += 1
            rows.append(
                {
                    "observation": observation,
                    "interpretation": interpretation,
                    "hypotheses": self.list_hypotheses(interpretation.id) if interpretation else [],
                }
            )
        return {
            "person_id": person_id,
            "observations": rows,
            "observation_count": len(observations),
            "interpretations_without_a_second_explanation": incomplete,
            "note": (
                "Every interpretation here is a set of possibilities, not a finding. "
                "None of them should be acted on without checking the others."
            ),
        }


def _normalise_hypotheses(
    hypotheses: Sequence[Dict[str, Any]], permitted: Sequence[str]
) -> List[Dict[str, Any]]:
    """Filter, re-rank and re-derive strength.

    The model does not get to set the strength. It is derived from how many
    supporting citations survived the trust guard, so a model asserting
    "supported" on one piece of evidence cannot promote itself.
    """
    allowed = set(permitted or [])
    cleaned: List[Dict[str, Any]] = []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        explanation = str(hypothesis.get("explanation") or "").strip()
        if not explanation:
            continue
        supporting = [
            eid for eid in (hypothesis.get("supporting_evidence_ids") or []) if isinstance(eid, str) and eid in allowed
        ]
        contradicting = [
            eid
            for eid in (hypothesis.get("contradicting_evidence_ids") or [])
            if isinstance(eid, str) and eid in allowed
        ]
        cleaned.append(
            {
                "explanation": explanation[:500],
                "supporting_evidence_ids": unique(supporting),
                "contradicting_evidence_ids": unique(contradicting),
                "why_plausible": (str(hypothesis.get("why_plausible") or "").strip() or None),
                "claimed_strength": _enum_value(hypothesis.get("strength")),
                "strength": _derive_strength(supporting, contradicting),
            }
        )
    cleaned.sort(key=lambda h: (len(h["supporting_evidence_ids"]), -len(h["contradicting_evidence_ids"])), reverse=True)
    return cleaned


def _derive_strength(
    supporting: Sequence[str], contradicting: Sequence[str]
) -> HypothesisStrength:
    if contradicting and len(contradicting) > len(supporting):
        return HypothesisStrength.CONTRADICTED
    if len(supporting) >= _SUPPORTED_MIN_EVIDENCE:
        return HypothesisStrength.SUPPORTED
    if supporting:
        return HypothesisStrength.POSSIBLE
    return HypothesisStrength.UNSUPPORTED


def _deterministic_interpretation(
    observation: BehaviorObservation,
    prior: Sequence[BehaviorObservation],
    known_triggers: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Explanations available with no model running.

    Still plural by construction. It offers the record's own triggers plus the
    standing considerations, and it says plainly that nothing here is
    established.
    """
    hypotheses: List[Dict[str, Any]] = []

    for trigger in known_triggers:
        if not trigger.get("trigger"):
            continue
        hypotheses.append(
            {
                "explanation": f"This has happened before in connection with: {trigger['trigger']}",
                "supporting_evidence_ids": [],
                "contradicting_evidence_ids": [],
                "why_plausible": (
                    "A trigger for this person is already on record, though nothing links it to "
                    "this particular occasion."
                ),
                "strength": HypothesisStrength.UNSUPPORTED,
            }
        )

    same_category = [
        o
        for o in prior
        if o.category is not None and observation.category is not None and o.category == observation.category
    ]
    for consideration in _CONSIDERATIONS[:4]:
        hypotheses.append(
            {
                "explanation": f"Worth ruling in or out: {consideration}",
                "supporting_evidence_ids": [],
                "contradicting_evidence_ids": [],
                "why_plausible": (
                    f"{len(same_category)} previous observation(s) of the same kind are on record. "
                    "Nothing in them establishes this as the explanation."
                ),
                "strength": HypothesisStrength.UNSUPPORTED,
            }
        )

    return {
        "hypotheses": hypotheses,
        "what_would_settle_it": [
            {"check": f"Record whether {item} was present around this occasion.", "decides": item}
            for item in _CONSIDERATIONS[:6]
        ],
        "summary": (
            f"Recorded as {_enum_value(observation.category)}. No explanation is established. "
            f"{len(hypotheses)} possibilities are open, none of them supported by evidence yet."
        ),
        "possible_context": (
            "Nothing in the record explains this behaviour yet. The most useful next step is recording "
            "what happened immediately before and after, and whether it has happened before."
        ),
    }


def _join(values: Sequence[str]) -> str:
    return ", ".join(values)


def _detail_map(result: LLMReasoningResult) -> Dict[str, Any]:
    detail: Dict[str, Any] = {}
    for item in result.detail or []:
        if isinstance(item, dict) and "field" in item:
            detail[item["field"]] = item.get("value")
    return detail


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _enum_or_value(value: Any, enum_cls) -> Any:
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    raw = _enum_value(value)
    try:
        return enum_cls(raw)
    except ValueError:
        fallback = getattr(enum_cls, "OTHER", None)
        return fallback if fallback is not None else value
