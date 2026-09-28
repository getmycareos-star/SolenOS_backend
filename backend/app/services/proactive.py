"""Reaching out before the caregiver asks.

This is the one place in the product where being wrong means interrupting
someone at a bad moment, so the design inverts the usual default. Silence is the
default and every message has to earn its way out.

A row existing does not mean anything should be sent. Suppression is applied
after the model has had its say, so a model that proposes contact during a crisis
is overruled rather than obeyed.

Four rules are enforced rather than requested:

No contact during a crisis. That moment needs a person, not a notification. If
there is an open safety question or a behaviour flagged as a safety concern, the
opportunity is suppressed whatever the model concludes.

Quiet hours are respected regardless of urgency the model reported. A caregiver
does not want to be woken, and "important" is not an exception to consent.

Frequency is capped. Two messages in a day is a relationship; six is a nuisance.

If nothing has changed, the answer is silence and the reason for it is recorded,
so the absence of a message is a decision rather than a gap in the product.
"""

from __future__ import annotations

from datetime import datetime, time as time_of_day
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.care_context_enums import EngagementTrigger, EngagementUrgency, FollowUpStatus
from app.core.llm_enums import LLMReasoningMode, LLMTask
from app.models.behavior import BehaviorObservation
from app.models.care import Evidence
from app.models.care_planning import CarePlan
from app.models.follow_up import EngagementOpportunity, FollowUp
from app.models.llm import LLMReasoningResult
from app.models.uncertainty import Contradiction as ContradictionModel
from app.services.care_common import (
    days_between,
    ensure_utc,
    evidence_map,
    parse_time,
    unique,
    utc_now,
)
from app.services.llm import LocalReasoningEngine

# Local time window in which nothing is sent, whatever the urgency. A caregiver
# being woken by a care app is how a care app becomes something uninstalled.
_QUIET_START = time_of_day(22, 0)
_QUIET_END = time_of_day(7, 0)
_MAX_PER_DAY = 2
# A change must be this recent to be worth interrupting anyone over.
_RECENT_CHANGE_DAYS = 3


class ProactiveEngine:
    def __init__(self, db: Session, engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self.engine = engine or LocalReasoningEngine(db)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_opportunities(
        self, person_id: str, as_of: Optional[Any] = None
    ) -> List[EngagementOpportunity]:
        return (
            self.db.query(EngagementOpportunity)
            .filter(EngagementOpportunity.person_id == person_id)
            .order_by(EngagementOpportunity.created_at.desc())
            .limit(50)
            .all()
        )

    def pending(self, person_id: str) -> List[EngagementOpportunity]:
        return [
            row
            for row in self.list_opportunities(person_id)
            if row.should_reach_out and not row.suppressed and not row.is_acted_on
        ]

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        person_id: str,
        as_of: Optional[Any] = None,
        caregiver_local_hour: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Decide whether to reach out. Silence is the default answer."""
        now = ensure_utc(parse_time(as_of)) or utc_now()
        candidates = self._candidates(person_id, now)
        crisis = self._crisis_state(person_id)

        opportunities: List[EngagementOpportunity] = []
        for candidate in candidates:
            opportunities.append(
                self._evaluate_candidate(
                    person_id=person_id,
                    candidate=candidate,
                    now=now,
                    hour=caregiver_local_hour,
                    crisis=crisis,
                )
            )

        sent_today = self._sent_today(person_id, now)
        remaining = max(0, _MAX_PER_DAY - sent_today)

        pending = [o for o in opportunities if o.should_reach_out and not o.suppressed]
        if remaining == 0 and pending:
            for opportunity in pending:
                opportunity.suppressed = True
                opportunity.suppression_reason = (
                    f"Daily limit reached ({_MAX_PER_DAY} already sent today). "
                    "Silencing is the right call rather than exceeding the cap."
                )
            pending = []

        return {
            "person_id": person_id,
            "evaluated_at": now,
            "in_crisis": bool(crisis["in_crisis"]),
            "crisis_detail": crisis["detail"],
            "sent_today": sent_today,
            "remaining_today": remaining,
            "quiet_hours": _in_quiet_hours(hour=caregiver_local_hour, now=now),
            "should_reach_out": bool(pending),
            "opportunities": opportunities,
            "candidates_considered": len(candidates),
            "note": (
                "No message is sent from this. A row here is a proposal that has passed the suppression "
                "rules, not an action taken."
            ),
        }

    def _candidates(self, person_id: str, now: datetime) -> List[Dict[str, Any]]:
        """Situations that could justify contact, each with the reason it exists."""
        candidates: List[Dict[str, Any]] = []

        for follow_up in (
            self.db.query(FollowUp)
            .filter(
                FollowUp.person_id == person_id,
                FollowUp.status == FollowUpStatus.SCHEDULED,
                FollowUp.due_at <= now,
            )
            .order_by(FollowUp.due_at.asc())
            .limit(5)
            .all()
        ):
            overdue_days = days_between(ensure_utc(follow_up.due_at), now) or 0
            candidates.append(
                {
                    "trigger": EngagementTrigger.FOLLOW_UP_DUE,
                    "subject_type": follow_up.subject_type,
                    "subject_id": follow_up.subject_id,
                    "reason_now": (
                        f"A re-check that was scheduled is {overdue_days} day(s) overdue: "
                        f"{follow_up.what_to_recheck}"
                    ),
                    "urgency_hint": EngagementUrgency.TODAY if overdue_days > 3 else EngagementUrgency.THIS_WEEK,
                    "evidence_ids": follow_up.evidence_ids or [],
                    "material": True,
                }
            )

        for contradiction in (
            self.db.query(ContradictionModel)
            .filter(
                ContradictionModel.person_id == person_id,
                ContradictionModel.resolution_status == "unresolved",
            )
            .limit(5)
            .all()
        ):
            age = days_between(ensure_utc(contradiction.created_at), now)
            if age is None or age > _RECENT_CHANGE_DAYS:
                continue
            candidates.append(
                {
                    "trigger": EngagementTrigger.CONTRADICTION_OPEN,
                    "subject_type": "contradiction",
                    "subject_id": contradiction.id,
                    "reason_now": (
                        f"Two records disagree about {contradiction.field or 'something'} and nobody has "
                        f"been asked. Opened {age} day(s) ago."
                    ),
                    "urgency_hint": EngagementUrgency.THIS_WEEK,
                    "evidence_ids": contradiction.evidence_ids or [],
                    "material": True,
                }
            )

        for plan in (
            self.db.query(CarePlan)
            .filter(CarePlan.person_id == person_id, CarePlan.is_active.is_(True))
            .limit(5)
            .all()
        ):
            if plan.review_at is None or ensure_utc(plan.review_at) > now:
                continue
            candidates.append(
                {
                    "trigger": EngagementTrigger.PLAN_REVIEW,
                    "subject_type": "care_plan",
                    "subject_id": plan.id,
                    "reason_now": f"A care plan reached its review date and has not been looked at: {plan.title}",
                    "urgency_hint": EngagementUrgency.THIS_WEEK,
                    "evidence_ids": plan.evidence_ids or [],
                    "material": True,
                }
            )

        recent = (
            self.db.query(Evidence)
            .filter(Evidence.person_id == person_id)
            .order_by(Evidence.uploaded_at.desc())
            .limit(1)
            .all()
        )
        if recent:
            quiet = days_between(ensure_utc(recent[0].uploaded_at), now)
            if quiet is not None and quiet > 7:
                candidates.append(
                    {
                        "trigger": EngagementTrigger.GUTTER_QUIET,
                        "subject_type": "record",
                        "subject_id": None,
                        "reason_now": (
                            f"Nothing has been written down for {quiet} days. This usually means the week "
                            "has been hard, not that nothing happened."
                        ),
                        "urgency_hint": EngagementUrgency.NOT_NOW,
                        "evidence_ids": [],
                        "material": False,
                    }
                )

        return candidates

    def _crisis_state(self, person_id: str) -> Dict[str, Any]:
        """Whether something is happening that a person must handle, not a message."""
        recent_safety = (
            self.db.query(BehaviorObservation)
            .filter(BehaviorObservation.person_id == person_id, BehaviorObservation.is_safety_concern.is_(True))
            .order_by(BehaviorObservation.occurred_at.desc().nullslast())
            .limit(3)
            .all()
        )
        if recent_safety:
            detail = "An open safety concern is on record: " + recent_safety[0].description
            return {"in_crisis": True, "detail": detail}
        return {"in_crisis": False, "detail": None}

    def _evaluate_candidate(
        self,
        person_id: str,
        candidate: Dict[str, Any],
        now: datetime,
        hour: Optional[int],
        crisis: Dict[str, Any],
    ) -> EngagementOpportunity:
        evidence_ids = unique(candidate.get("evidence_ids") or [])
        texts = evidence_map(self.db, evidence_ids)

        result: Optional[LLMReasoningResult] = self.engine.reason(
            task=LLMTask.PROACTIVE_OPPORTUNITY,
            person_id=person_id,
            subject_type=candidate.get("subject_type"),
            subject_id=candidate.get("subject_id"),
            payload={
                "trigger": _enum_value(candidate["trigger"]),
                "reason_now": candidate.get("reason_now"),
                "urgency_hint": _enum_value(candidate.get("urgency_hint")),
                "how_quiet_the_record_has_been": True,
                "evidence": texts,
            },
            evidence_ids=evidence_ids,
            evidence_texts=texts,
            mode=LLMReasoningMode.LLM_ASSISTED,
            require_evidence=False,
        )

        if result is None:
            # No model, no message. Silence by default.
            opportunity = EngagementOpportunity(
                person_id=person_id,
                trigger=candidate["trigger"],
                subject_type=candidate.get("subject_type"),
                subject_id=candidate.get("subject_id"),
                should_reach_out=False,
                reason_now=candidate.get("reason_now"),
                why_quiet_is_better=(
                    "There is a reason to consider contacting, but no local reasoning available to judge "
                    "whether it is worth interrupting anyone. Silence is the safe answer."
                ),
                urgency=EngagementUrgency.NOT_NOW,
                evidence_ids=evidence_ids,
                possible_context="Whether contact is warranted has not been assessed.",
                confidence=None,
                reasoning_result_id=None,
            )
            self.db.add(opportunity)
            self.db.flush()
            return opportunity

        detail = _detail_map(result)
        should = bool(detail.get("should_reach_out"))
        what_to_say = detail.get("what_to_say")
        why_quiet = detail.get("why_quiet_is_better")
        urgency = _enum_or_value(detail.get("urgency"), EngagementUrgency)

        opportunity = EngagementOpportunity(
            person_id=person_id,
            trigger=candidate["trigger"],
            subject_type=candidate.get("subject_type"),
            subject_id=candidate.get("subject_id"),
            should_reach_out=should,
            reason_now=candidate.get("reason_now"),
            what_to_say=_clean(what_to_say) if should else None,
            urgency=urgency if should else EngagementUrgency.NOT_NOW,
            why_quiet_is_better=why_quiet,
            channel="in_app",
            evidence_ids=unique(list(result.evidence_ids or []) + evidence_ids),
            possible_context=result.possible_context,
            confidence=result.confidence,
            reasoning_result_id=result.id,
        )
        self.db.add(opportunity)
        self.db.flush()
        self._suppress_if_needed(opportunity, now=now, hour=hour, crisis=crisis)
        return opportunity

    def _suppress_if_needed(
        self,
        opportunity: EngagementOpportunity,
        now: datetime,
        hour: Optional[int],
        crisis: Dict[str, Any],
    ) -> EngagementOpportunity:
        """Apply the rules the model does not get to override."""
        if not opportunity.should_reach_out:
            return opportunity

        if crisis.get("in_crisis"):
            opportunity.should_reach_out = False
            opportunity.suppressed = True
            opportunity.suppression_reason = (
                "Something on record needs a person, not a notification. " + str(crisis.get("detail"))
            )
            opportunity.urgency = EngagementUrgency.NOT_NOW
            opportunity.what_to_say = None
            return opportunity

        if _in_quiet_hours(hour=hour, now=now):
            opportunity.should_reach_out = False
            opportunity.suppressed = True
            opportunity.quiet_hours_respected = False
            opportunity.suppression_reason = (
                "It is between 22:00 and 07:00. Quiet hours are not overridden by urgency, because a "
                "caregiver being woken is how this becomes something uninstalled."
            )
            opportunity.urgency = EngagementUrgency.THIS_WEEK
            return opportunity

        if not (opportunity.what_to_say or "").strip():
            opportunity.should_reach_out = False
            opportunity.suppressed = True
            opportunity.suppression_reason = "The proposed message was empty. Nothing to send."
            opportunity.urgency = EngagementUrgency.NOT_NOW
            return opportunity

        if opportunity.urgency == EngagementUrgency.NOW:
            # Nothing in this product justifies waking anyone.
            opportunity.urgency = EngagementUrgency.TODAY
            opportunity.reason_now = (
                (opportunity.reason_now or "")
                + " [urgency reduced: this system does not send 'now' notifications]"
            ).strip()
        return opportunity

    def _sent_today(self, person_id: str, now: datetime) -> int:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return len(
            [
                row
                for row in self.list_opportunities(person_id)
                if row.is_acted_on and ensure_utc(row.acted_at) and ensure_utc(row.acted_at) >= start
            ]
        )

    def mark_acted_on(self, opportunity_id: str) -> EngagementOpportunity:
        opportunity = (
            self.db.query(EngagementOpportunity)
            .filter(EngagementOpportunity.id == opportunity_id)
            .first()
        )
        if opportunity is None:
            raise ValueError("Engagement opportunity not found")
        if opportunity.suppressed:
            raise ValueError("A suppressed opportunity cannot be sent")
        if not opportunity.should_reach_out:
            raise ValueError("This opportunity concluded that no contact should be made")
        opportunity.is_acted_on = True
        opportunity.acted_at = utc_now()
        self.db.flush()
        return opportunity

    def add_silence_note(self, person_id: str, reason: str) -> EngagementOpportunity:
        """Record that we decided not to reach out, and why.

        The absence of a message should be a decision that can be inspected, not
        a gap where something might have gone.
        """
        row = EngagementOpportunity(
            person_id=person_id,
            trigger=EngagementTrigger.SCHEDULED_REVIEW,
            should_reach_out=False,
            why_quiet_is_better=reason,
            urgency=EngagementUrgency.NOT_NOW,
            evidence_ids=[],
            possible_context=reason,
        )
        self.db.add(row)
        self.db.flush()
        return row


def _in_quiet_hours(hour: Optional[int], now: datetime) -> bool:
    if hour is not None:
        return hour >= _QUIET_START.hour or hour < _QUIET_END.hour
    return now.hour >= _QUIET_START.hour or now.hour < _QUIET_END.hour


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
        fallback = getattr(enum_cls, "NOT_NOW", None) or list(enum_cls)[0]
        return fallback
