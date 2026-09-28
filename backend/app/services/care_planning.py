"""Solving, not just understanding.

The rest of the system produces state. This is where state becomes something a
caregiver can act on, and it is the layer most likely to do harm if it is
allowed to be confident, because a plan reads like an instruction.

Four constraints are structural:

Options are mandatory and "do nothing and watch" is a real option. A decision
that arrives without its alternatives is not a decision. Monitoring is frequently
the right answer and it should never look like the absent one.

Nothing here is ever accepted automatically. A decision stays proposed until a
person accepts it, because the person in the room has information the record does
not.

Medical decisions are routed, not made. Where the decision belongs to a clinician
the output says what to raise with them, and the decision owner is recorded as
the clinician. The trust guard rejects medication instructions at the language
layer, and this layer refuses to present a clinician's decision as a caregiver's.

Alignment is checked before a plan is built. A plan that is efficient and ignores
what this person values is not a good plan, and the assessment is carried onto
the plan rather than left behind in another table.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.care_context_enums import (
    DecisionOwner,
    DecisionStatus,
    PlanStatus,
)
from app.core.llm_enums import LLMReasoningMode, LLMTask
from app.models.care_planning import CareDecision, CarePlan, CarePlanStep
from app.models.llm import LLMReasoningResult
from app.services.care_common import (
    ensure_utc,
    evidence_map,
    unique,
    utc_now,
)
from app.services.llm import LocalReasoningEngine

_MIN_OPTIONS = 2
_DEFAULT_REVIEW_DAYS = 14

_MEDICAL_MARKERS = (
    "medication",
    "dose",
    "dosage",
    "prescription",
    "prescribe",
    "diagnos",
    "treatment",
    "therapy",
    "sedative",
    "antipsychotic",
    "painkiller",
    "antibiotic",
    "blood pressure",
    "insulin",
)


class CarePlannerEngine:
    def __init__(self, db: Session, engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self.engine = engine or LocalReasoningEngine(db)

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def propose_decision(
        self,
        person_id: str,
        question: str,
        context: Optional[str] = None,
        urgency: Optional[str] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
    ) -> CareDecision:
        """Put a real decision to a caregiver, with its alternatives."""
        if not question or not question.strip():
            raise ValueError("A decision needs a question")

        texts = evidence_map(self.db, evidence_ids or [])
        is_medical = _is_medical(question)
        fallback = _deterministic_decision(question, is_medical)

        result: Optional[LLMReasoningResult] = None
        options: List[Dict[str, Any]] = fallback["options"]
        recommendation: Dict[str, Any] = fallback["recommendation"]
        deciding_factors: List[str] = fallback["deciding_factors"]
        summary = fallback["summary"]
        possible_context = fallback["possible_context"]
        confidence: Optional[float] = None
        owner = fallback["owner"]

        model_result = self.engine.reason(
            task=LLMTask.DECISION_OPTIONS,
            person_id=person_id,
            subject_type=subject_type or "decision",
            subject_id=subject_id,
            payload={
                "decision_to_make": question,
                "context": context,
                "urgency": urgency,
                "is_a_clinical_decision": is_medical,
                "what_this_person_values": _values_view(self, person_id),
                "open_questions": _open_questions_view(self, person_id),
                "evidence": texts,
            },
            evidence_ids=evidence_ids,
            evidence_texts=texts,
            mode=LLMReasoningMode.LLM_ASSISTED,
        )
        if model_result is not None:
            result = model_result
            detail = _detail_map(result)
            proposed = [o for o in (detail.get("options") or []) if isinstance(o, dict)]
            if proposed:
                options = _normalise_options(proposed)
            recommendation = detail.get("recommendation") if isinstance(detail.get("recommendation"), dict) else recommendation
            deciding_factors = _string_list(detail.get("deciding_factors")) or deciding_factors
            owner = _enum_or_value(detail.get("decision_owner"), DecisionOwner) or owner
            summary = result.summary
            possible_context = result.possible_context or possible_context
            confidence = result.confidence

        if is_medical:
            # A clinical decision does not become a caregiver's decision because
            # the language model answered confidently about it.
            owner = DecisionOwner.CLINICIAN
        options = _ensure_monitoring_option(options)
        requires_clinician = is_medical or owner == DecisionOwner.CLINICIAN

        decision = CareDecision(
            person_id=person_id,
            question=question.strip(),
            context=context,
            urgency=urgency,
            status=DecisionStatus.PROPOSED,
            owner=owner,
            options=options,
            option_count=len(options),
            recommendation=recommendation,
            deciding_factors=deciding_factors,
            summary=summary,
            safety_decision=(
                "clinical_decision_not_made_here" if requires_clinician else "no_clinical_action_proposed"
            ),
            requires_clinician=requires_clinician,
            requires_human_review=True,
            evidence_ids=list(result.evidence_ids or []) if result is not None else list(evidence_ids or []),
            possible_context=possible_context,
            confidence=confidence,
            reasoning_result_id=result.id if result is not None else None,
        )
        self.db.add(decision)
        self.db.flush()
        return decision

    def decide(
        self,
        decision_id: str,
        accept: bool,
        note: Optional[str] = None,
        caregiver_id: Optional[str] = None,
    ) -> CareDecision:
        """A person accepts or rejects. Never done automatically."""
        decision = self.db.query(CareDecision).filter(CareDecision.id == decision_id).first()
        if decision is None:
            raise ValueError("Care decision not found")
        if decision.status != DecisionStatus.PROPOSED:
            raise ValueError(f"Decision is already {decision.status.value}")
        decision.status = DecisionStatus.ACCEPTED if accept else DecisionStatus.REJECTED
        decision.decided_at = utc_now()
        decision.decided_by_caregiver_id = caregiver_id
        decision.decision_note = note
        self.db.flush()
        return decision

    def list_decisions(
        self, person_id: str, status: Optional[Any] = None
    ) -> List[CareDecision]:
        query = self.db.query(CareDecision).filter(CareDecision.person_id == person_id)
        if status is not None:
            query = query.filter(CareDecision.status == _enum_or_value(status, DecisionStatus))
        return query.order_by(CareDecision.created_at.desc()).all()

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------

    def build_plan(
        self,
        person_id: str,
        decision_id: Optional[str] = None,
        title: Optional[str] = None,
        evidence_ids: Optional[Sequence[str]] = None,
    ) -> CarePlan:
        """Turn a recommended decision into steps that will actually be followed."""
        decision = None
        if decision_id:
            decision = self.db.query(CareDecision).filter(CareDecision.id == decision_id).first()
            if decision is None:
                raise ValueError("Care decision not found")

        question = decision.question if decision else (title or "Current care approach")
        combined_ids = unique(
            list(evidence_ids or []) + (decision.evidence_ids or [] if decision else [])
        )
        texts = evidence_map(self.db, combined_ids)

        fallback = _deterministic_plan(person_id, decision)
        steps = fallback["steps"]
        stop_signs = fallback["stop_and_escalate_if"]
        if_not_working = fallback["if_it_is_not_working"]
        review_days = _DEFAULT_REVIEW_DAYS
        summary = fallback["summary"]
        possible_context = fallback["possible_context"]
        confidence: Optional[float] = None
        result: Optional[LLMReasoningResult] = None

        model_result = self.engine.reason(
            task=LLMTask.DECISION_PLAN,
            person_id=person_id,
            subject_type="care_plan",
            subject_id=decision_id,
            payload={
                "decision": question,
                "decision_context": decision.context if decision else None,
                "recommended_option": decision.recommendation if decision else None,
                "what_this_person_values": _values_view(self, person_id),
                "known_triggers_and_calmers": _person_practices_view(self, person_id),
                "evidence": texts,
            },
            evidence_ids=combined_ids,
            evidence_texts=texts,
            mode=LLMReasoningMode.LLM_ASSISTED,
        )
        if model_result is not None:
            result = model_result
            detail = _detail_map(result)
            proposed = [s for s in (detail.get("steps") or []) if isinstance(s, dict)]
            if proposed:
                steps = [
                    {
                        "step": str(s.get("step") or "").strip(),
                        "owner": s.get("owner"),
                        "when": s.get("when"),
                        "how_we_know_it_worked": s.get("how_we_know_it_worked"),
                    }
                    for s in proposed
                    if str(s.get("step") or "").strip()
                ]
            stop_signs = _string_list(detail.get("stop_and_escalate_if")) or stop_signs
            if_not_working = detail.get("if_it_is_not_working") or if_not_working
            review_days = _review_days(detail.get("review_at"))
            summary = result.summary
            possible_context = result.possible_context or possible_context
            confidence = result.confidence

        steps = [s for s in steps if s.get("step")]
        summary = _append_alignment_note(self, person_id, decision, summary)

        plan = CarePlan(
            person_id=person_id,
            decision_id=decision_id,
            title=title or (decision.question[:120] if decision else "Care plan"),
            description=decision.context if decision else None,
            status=PlanStatus.ACTIVE,
            steps=steps,
            step_count=len(steps),
            review_at=utc_now() + _days(review_days),
            stop_and_escalate_if=stop_signs,
            if_it_is_not_working=if_not_working,
            summary=summary,
            evidence_ids=list(result.evidence_ids or []) if result is not None else combined_ids,
            possible_context=possible_context,
            confidence=confidence,
            reasoning_result_id=result.id if result is not None else None,
        )
        self.db.add(plan)
        self.db.flush()

        for index, step in enumerate(steps):
            self.db.add(
                CarePlanStep(
                    plan_id=plan.id,
                    person_id=person_id,
                    sequence=index + 1,
                    step=step["step"],
                    owner=step.get("owner"),
                    when=step.get("when"),
                    how_we_know_it_worked=step.get("how_we_know_it_worked"),
                )
            )
        self.db.flush()
        return plan

    def list_plans(self, person_id: str, status: Optional[Any] = None) -> List[CarePlan]:
        query = self.db.query(CarePlan).filter(
            CarePlan.person_id == person_id, CarePlan.is_active.is_(True)
        )
        if status is not None:
            query = query.filter(CarePlan.status == _enum_or_value(status, PlanStatus))
        return query.order_by(CarePlan.created_at.desc()).all()

    def complete_step(
        self, step_id: str, worked: Optional[bool] = None
    ) -> CarePlanStep:
        step = self.db.query(CarePlanStep).filter(CarePlanStep.id == step_id).first()
        if step is None:
            raise ValueError("Care plan step not found")
        step.is_completed = True
        step.completed_at = utc_now()
        if worked is False:
            step.how_we_know_it_worked = (step.how_we_know_it_worked or "") + " [reported as not working]"
        self.db.flush()
        return step

    def plan_progress(self, plan_id: str) -> Dict[str, Any]:
        plan = self.db.query(CarePlan).filter(CarePlan.id == plan_id).first()
        if plan is None:
            raise ValueError("Care plan not found")
        steps = (
            self.db.query(CarePlanStep)
            .filter(CarePlanStep.plan_id == plan_id)
            .order_by(CarePlanStep.sequence.asc())
            .all()
        )
        done = [s for s in steps if s.is_completed]
        # SQLite returns datetimes without a timezone, so this has to be
        # normalised before it is compared with an aware "now".
        review_at = ensure_utc(plan.review_at)
        return {
            "plan": plan,
            "steps": steps,
            "completed": len(done),
            "total": len(steps),
            "review_due": review_at is not None and review_at <= utc_now(),
        }


def _normalise_options(options: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned = []
    for option in options:
        if not isinstance(option, dict):
            continue
        text = str(option.get("option") or "").strip()
        if not text:
            continue
        cleaned.append(
            {
                "option": text[:300],
                "what_it_involves": option.get("what_it_involves"),
                "benefits": _string_list(option.get("benefits")),
                "risks": _string_list(option.get("risks")),
                "requires": _string_list(option.get("requires")),
            }
        )
    return cleaned


def _ensure_monitoring_option(options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Make sure monitoring is on the table, and that there is a choice at all.

    Two separate failures to guard against. If the model left out doing
    nothing, it goes in explicitly, because the option a caregiver cannot see
    is the one they cannot choose. And if only one option survived, another is
    added: a single option is an instruction, not a decision.
    """
    monitoring_terms = ("do nothing", "watch", "monitor", "wait", "no change", "stay as")
    has_monitoring = any(
        any(term in str(option.get("option", "")).lower() for term in monitoring_terms)
        for option in options
    )
    if not has_monitoring:
        options = options + [_MONITORING_OPTION]

    if len(options) < _MIN_OPTIONS:
        options = options + [_ACTION_OPTION]
    return options


_MONITORING_OPTION = {
    "option": "Do nothing for now, and watch closely",
    "what_it_involves": "Keep recording what happens, and set a time to decide again.",
    "benefits": ["Avoids acting on something that may resolve itself"],
    "risks": ["If it is getting worse, waiting makes it worse"],
    "requires": ["Something being written down while waiting"],
}

_ACTION_OPTION = {
    "option": "Make the change, and watch what happens",
    "what_it_involves": "Do the thing, keep a short record, and review it.",
    "benefits": ["Acts on the question rather than leaving it open"],
    "risks": ["Could make things worse rather than better"],
    "requires": ["A way to tell whether it helped"],
}


def _append_alignment_note(
    engine: "CarePlannerEngine", person_id: str, decision: Optional[CareDecision], summary: str
) -> str:
    """Carry the what-matters check onto the plan.

    A plan that is efficient and ignores what this person values should say so
    where the plan is read, not only in a separate table nobody opens.
    """
    from app.services.what_matters import WhatMattersEngine

    if decision is None or not decision.question:
        return summary
    try:
        # The planner's own reasoning engine is threaded through rather than
        # letting this build a second one, so a single request uses a single
        # reasoning stack and a single view of what is available.
        assessment = WhatMattersEngine(engine.db, engine.engine).assess_action(
            person_id=person_id,
            action_description=decision.question,
            action_ref_type="care_decision",
            action_ref_id=decision.id,
        )
    except Exception:
        return summary

    verdict = _enum_value(assessment.verdict)
    if verdict in ("does_not_serve", "conflicts"):
        return (
            f"{summary} Note: this does not clearly serve what is recorded as mattering to this person "
            f"({assessment.reasoning or 'see the alignment assessment'})."
        )
    if verdict == "unknown" and assessment.possible_context:
        return f"{summary} {assessment.possible_context}"
    return summary


def _deterministic_decision(question: str, is_medical: bool) -> Dict[str, Any]:
    options = [
        {
            "option": "Do nothing for now, and watch closely",
            "what_it_involves": "Keep recording what happens, and set a time to decide again.",
            "benefits": ["Avoids acting on something that may resolve itself"],
            "risks": ["If it is getting worse, waiting makes it worse"],
            "requires": ["Something being written down while waiting"],
        }
    ]
    if is_medical:
        options.insert(
            0,
            {
                "option": "Raise it with the clinician and let them decide",
                "what_it_involves": "Bring the question and the record of what has been happening.",
                "what_it_involves_note": None,
                "benefits": ["The decision goes to someone qualified to make it"],
                "risks": ["Takes time"],
                "requires": ["An appointment or a call"],
            },
        )
    return {
        "options": options,
        "recommendation": {
            "option": options[0]["option"],
            "why": (
                "This is a clinical decision, so the first option is the one that routes it to a clinician."
                if is_medical
                else "Monitoring is the safest default while the record is this thin."
            ),
            "confidence_caveat": "With little recorded, this is a starting point rather than a recommendation.",
        },
        "deciding_factors": [
            "What is actually recorded, as opposed to assumed",
            "What this person values",
            "What the least intrusive effective option is",
        ],
        "owner": DecisionOwner.CAREGIVER,
        "summary": (
            f"A decision is open about: {question}. "
            + (
                "It is a clinical decision, so it belongs to a clinician. What follows is what to raise with them."
                if is_medical
                else "It belongs to the caregiver, and the alternatives are set out below."
            )
        ),
        "possible_context": (
            "There is not much recorded that bears on this. The options below are a starting point for a "
            "conversation rather than a set of findings."
        ),
    }


def _deterministic_plan(
    person_id: str, decision: Optional[CareDecision]
) -> Dict[str, Any]:
    return {
        "steps": [
            {
                "step": "Write down what happens, and when.",
                "owner": "caregiver",
                "when": "each time it happens, for the next week",
                "how_we_know_it_worked": "There is something written down to look at.",
            },
            {
                "step": "Look at what is recorded at the review point.",
                "owner": "caregiver",
                "when": "at the review date",
                "how_we_know_it_worked": "A decision can be made on something other than a guess.",
            },
        ],
        "stop_and_escalate_if": [
            "It gets worse rather than better",
            "Anything suggesting an emergency",
        ],
        "if_it_is_not_working": (
            "Nothing has been decided by the review date. Escalate rather than extending the watching."
        ),
        "summary": (
            "A deliberately small plan. It assumes an exhausted caregiver rather than an ideal one, "
            "because a longer plan does not get followed."
        ),
        "possible_context": (
            "This plan is built from a thin record. It is a place to start, not a settled course of action."
        ),
    }


def _is_medical(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _MEDICAL_MARKERS)


def _values_view(engine: "CarePlannerEngine", person_id: str) -> List[Dict[str, Any]]:
    from app.services.what_matters import WhatMattersEngine

    try:
        entries = WhatMattersEngine(engine.db).list_entries(person_id)
    except Exception:
        return []
    return [
        {
            "statement": e.statement,
            "importance": _enum_value(e.importance),
            "what_it_rules_out": e.what_it_rules_out or [],
        }
        for e in entries
    ]


def _person_practices_view(engine: "CarePlannerEngine", person_id: str) -> Dict[str, Any]:
    from app.services.person_model import PersonModelEngine

    try:
        model = PersonModelEngine(engine.db)
        return {
            "triggers": [
                {"trigger": t.trigger, "what_happens": t.response_seen, "what_helps": t.calming_measure}
                for t in model.list_triggers(person_id)
            ],
            "calming_measures": [c.measure for c in model.list_calmers(person_id)],
        }
    except Exception:
        return {"triggers": [], "calming_measures": []}


def _open_questions_view(engine: "CarePlannerEngine", person_id: str) -> List[str]:
    from app.models.uncertainty import OpenQuestion

    try:
        rows = (
            engine.db.query(OpenQuestion)
            .filter(OpenQuestion.person_id == person_id, OpenQuestion.status == "open")
            .limit(5)
            .all()
        )
    except Exception:
        return []
    return [row.question for row in rows]


def _detail_map(result: LLMReasoningResult) -> Dict[str, Any]:
    detail: Dict[str, Any] = {}
    for item in result.detail or []:
        if isinstance(item, dict) and "field" in item:
            detail[item["field"]] = item.get("value")
    return detail


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in (None, "")]


def _review_days(value: Any) -> int:
    if value is None:
        return _DEFAULT_REVIEW_DAYS
    text = str(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return _DEFAULT_REVIEW_DAYS
    try:
        return max(1, min(365, int(digits)))
    except ValueError:
        return _DEFAULT_REVIEW_DAYS


def _days(count: int):
    from datetime import timedelta

    return timedelta(days=count)


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
        fallback = getattr(enum_cls, "CAREGIVER", None) or list(enum_cls)[0]
        return fallback
