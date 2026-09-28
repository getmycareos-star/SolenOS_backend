"""Follow-ups as scheduled re-checks.

The repo has appointments and follow-up candidates. This is a different thing: a
question that was left open, with a date on which somebody should go and look
at it again. Not a reminder to make a note. Not a task for the caregiver to
manage. A re-check of something the record was not good enough to conclude.

The interval is derived from how long the uncertainty is tolerable for that
particular question, not from a default. A new medication wants a look in two
days. Whether a decline is real wants three weeks of readings. "When should we
revisit this" is a clinical judgement, so the model is asked for the interval
while the due date is still computed and recorded from it, and the reasoning is
stored so the number can be argued with.

Deliberately sparse. The failure mode of a follow-up system is a list nobody
reads, so the engine returns what is worth re-checking and, separately, what
looks open but does not need it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.care_context_enums import FollowUpStatus, FollowUpTrigger
from app.core.llm_enums import LLMReasoningMode, LLMTask
from app.models.follow_up import FollowUp
from app.models.llm import LLMReasoningResult
from app.models.trajectory import Trajectory
from app.models.uncertainty import Contradiction as ContradictionModel, InformationGap
from app.services.care_common import (
    days_between,
    ensure_utc,
    evidence_map,
    parse_time,
    utc_now,
)
from app.services.llm import LocalReasoningEngine

# How long a question can stay open before it is re-checked, by trigger. The
# medical defaults are conservative on purpose: a new medication is a two-day
# question, not a two-week one.
_DEFAULT_INTERVAL = {
    FollowUpTrigger.SAFETY: 1,
    FollowUpTrigger.MEDICATION_REVIEW if hasattr(FollowUpTrigger, "MEDICATION_REVIEW") else FollowUpTrigger.SAFETY: 2,
    FollowUpTrigger.TRAJECTORY: 21,
    FollowUpTrigger.CONTRADICTION: 7,
    FollowUpTrigger.OPEN_QUESTION: 14,
    FollowUpTrigger.UNCERTAINTY: 14,
    FollowUpTrigger.PLANNED_REVIEW: 14,
    FollowUpTrigger.BASELINE: 90,
}
_MAX_INTERVAL_DAYS = 180


class FollowUpEngine:
    def __init__(self, db: Session, engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self.engine = engine or LocalReasoningEngine(db)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def list_for_person(
        self,
        person_id: str,
        status: Optional[Any] = None,
        due_before: Optional[Any] = None,
        limit: int = 100,
    ) -> List[FollowUp]:
        query = self.db.query(FollowUp).filter(
            FollowUp.person_id == person_id, FollowUp.is_active.is_(True)
        )
        if status is not None:
            query = query.filter(FollowUp.status == _enum_or_value(status, FollowUpStatus))
        if due_before is not None:
            query = query.filter(FollowUp.due_at <= ensure_utc(parse_time(due_before)))
        return query.order_by(FollowUp.due_at.asc()).limit(limit).all()

    def due(
        self, person_id: str, as_of: Optional[Any] = None, grace_days: int = 0
    ) -> List[FollowUp]:
        now = ensure_utc(parse_time(as_of)) or utc_now()
        cutoff = now + timedelta(days=grace_days)
        return [
            follow_up
            for follow_up in self.list_for_person(person_id, status=FollowUpStatus.SCHEDULED)
            if ensure_utc(follow_up.due_at) is not None and ensure_utc(follow_up.due_at) <= cutoff
        ]

    def overdue(self, person_id: str, as_of: Optional[Any] = None) -> List[FollowUp]:
        now = ensure_utc(parse_time(as_of)) or utc_now()
        return [
            follow_up
            for follow_up in self.list_for_person(person_id, status=FollowUpStatus.SCHEDULED)
            if ensure_utc(follow_up.due_at) is not None and ensure_utc(follow_up.due_at) < now
        ]

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def schedule(
        self,
        person_id: str,
        what_to_recheck: str,
        trigger: Any = FollowUpTrigger.UNCERTAINTY,
        interval_days: Optional[int] = None,
        due_at: Optional[Any] = None,
        why_open: Optional[str] = None,
        what_result_changes_the_plan: Optional[str] = None,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        field: Optional[str] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        cadence: Optional[str] = None,
        priority_hint: Optional[str] = None,
    ) -> FollowUp:
        if not what_to_recheck or not what_to_recheck.strip():
            raise ValueError("A follow-up needs something to re-check")
        trigger_value = _enum_or_value(trigger, FollowUpTrigger)
        interval = interval_days or _default_interval(trigger_value)
        moment = ensure_utc(parse_time(due_at)) or (utc_now() + timedelta(days=interval))

        follow_up = FollowUp(
            person_id=person_id,
            subject_type=subject_type,
            subject_id=subject_id,
            field=field,
            what_to_recheck=what_to_recheck.strip(),
            why_open=why_open,
            what_result_changes_the_plan=what_result_changes_the_plan,
            trigger=trigger_value,
            status=FollowUpStatus.SCHEDULED,
            due_at=moment,
            interval_days=interval,
            cadence=cadence,
            priority_hint=priority_hint,
            evidence_ids=list(evidence_ids) if evidence_ids else None,
            possible_context=(
                "No reason has been recorded for this interval, so it is a default rather than a judgement."
                if why_open is None
                else None
            ),
        )
        self.db.add(follow_up)
        self.db.flush()
        return follow_up

    def schedule_for_trajectory(self, person_id: str, trajectory_id: str) -> FollowUp:
        """A trajectory under a decline gets re-checked, not a decline itself.

        The re-check is on whether the decline is still there, which is a
        different question from whether it is real.
        """
        trajectory = self.db.query(Trajectory).filter(Trajectory.id == trajectory_id).first()
        if trajectory is None:
            raise ValueError("Trajectory not found")
        return self.schedule(
            person_id=person_id,
            what_to_recheck=(
                f"Whether '{trajectory.label}' is still moving in the same direction ({trajectory.point_count} readings so far)."
            ),
            trigger=FollowUpTrigger.TRAJECTORY,
            interval_days=_interval_from_confidence(trajectory.confidence, _default_interval(FollowUpTrigger.TRAJECTORY)),
            why_open=(
                "A direction has been computed from a small number of readings. Enough further readings "
                "will show whether it holds."
            ),
            what_result_changes_the_plan=(
                "If the direction holds, this is a real change. If it flattens, it was ordinary variation."
            ),
            subject_type="trajectory",
            subject_id=trajectory.id,
            evidence_ids=trajectory.evidence_ids,
        )

    def schedule_for_contradiction(self, person_id: str, contradiction_id: str) -> FollowUp:
        contradiction = (
            self.db.query(ContradictionModel)
            .filter(ContradictionModel.id == contradiction_id)
            .first()
        )
        if contradiction is None:
            raise ValueError("Contradiction not found")
        return self.schedule(
            person_id=person_id,
            what_to_recheck=f"Whether the disagreement about {contradiction.field or 'this'} has been put to someone.",
            trigger=FollowUpTrigger.CONTRADICTION,
            why_open=(
                "Two records disagree and nothing has resolved it. Until one does, neither can be relied on."
            ),
            what_result_changes_the_plan=(
                "An answer from someone who was present, or a clinician's record, settles which to use."
            ),
            subject_type="contradiction",
            subject_id=contradiction.id,
            evidence_ids=contradiction.evidence_ids,
        )

    def schedule_for_gap(self, person_id: str, gap_id: str) -> FollowUp:
        gap = self.db.query(InformationGap).filter(InformationGap.id == gap_id).first()
        if gap is None:
            raise ValueError("Information gap not found")
        return self.schedule(
            person_id=person_id,
            what_to_recheck=f"Whether we can now establish: {gap.field}",
            trigger=FollowUpTrigger.OPEN_QUESTION,
            why_open=gap.gap_reason.value if hasattr(gap.gap_reason, "value") else "not established",
            what_result_changes_the_plan="A value, or a clear statement that it cannot be established.",
            subject_type=gap.subject_type,
            subject_id=gap.subject_id,
            field=gap.field,
            evidence_ids=gap.evidence_ids,
        )

    def schedule_from_open_state(
        self, person_id: str, as_of: Optional[Any] = None
    ) -> List[FollowUp]:
        """Find what is open in the record and put re-checks on it.

        Swept rather than asked for, so a gap nobody explicitly scheduled a
        follow-up for still gets one. Deduplicated against what is already
        scheduled for the same subject and field.
        """
        existing = {
            (f.subject_type, f.subject_id, f.field)
            for f in self.list_for_person(person_id, status=FollowUpStatus.SCHEDULED)
        }
        created: List[FollowUp] = []

        for trajectory in (
            self.db.query(Trajectory)
            .filter(
                Trajectory.person_id == person_id,
                Trajectory.is_active.is_(True),
                Trajectory.point_count < 5,
            )
            .all()
        ):
            key = ("trajectory", trajectory.id, None)
            if key in existing:
                continue
            existing.add(key)
            created.append(self.schedule_for_trajectory(person_id, trajectory.id))

        for contradiction in (
            self.db.query(ContradictionModel)
            .filter(
                ContradictionModel.person_id == person_id,
                ContradictionModel.resolution_status == "unresolved",
            )
            .limit(10)
            .all()
        ):
            key = ("contradiction", contradiction.id, contradiction.field)
            if key in existing:
                continue
            existing.add(key)
            created.append(self.schedule_for_contradiction(person_id, contradiction.id))

        for gap in (
            self.db.query(InformationGap)
            .filter(
                InformationGap.person_id == person_id,
                InformationGap.lifecycle_status == "open",
            )
            .limit(10)
            .all()
        ):
            key = (gap.subject_type, gap.subject_id, gap.field)
            if key in existing:
                continue
            existing.add(key)
            created.append(self.schedule_for_gap(person_id, gap.id))

        return created

    def rationale_for(
        self,
        person_id: str,
        what_to_recheck: str,
        evidence_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Ask what is genuinely worth re-checking, and when.

        Returns both the rechecks and an explicit list of things that look open
        but do not need a follow-up, which is the half of the answer that stops
        the list growing without limit.
        """
        evidence_list = list(evidence_ids or [])
        texts = evidence_map(self.db, evidence_list)
        result = self.engine.reason(
            task=LLMTask.FOLLOW_UP_RATIONALE,
            person_id=person_id,
            subject_type="follow_up",
            payload={
                "open_question": what_to_recheck,
                "currently_scheduled": [
                    f.what_to_recheck for f in self.list_for_person(person_id, status=FollowUpStatus.SCHEDULED)
                ],
                "trajectories": _trajectories_view(self, person_id),
                "open_contradictions": [
                    c.field for c in self.list_open_contradictions(person_id)
                ],
                "evidence": texts,
            },
            evidence_ids=evidence_list,
            evidence_texts=texts,
            mode=LLMReasoningMode.LLM_ASSISTED,
        )
        if result is None:
            return {
                "rechecks": [],
                "not_worth_rechecking": [],
                "possible_context": "No local reasoning available; schedule with the default interval.",
            }
        detail = _detail_map(result)
        return {
            "rechecks": [r for r in (detail.get("rechecks") or []) if isinstance(r, dict)],
            "not_worth_rechecking": _string_list(detail.get("not_worth_rechecking")),
            "summary": result.summary,
            "possible_context": result.possible_context,
            "confidence": result.confidence,
            "evidence_ids": result.evidence_ids,
        }

    def list_open_contradictions(self, person_id: str) -> List[ContradictionModel]:
        return (
            self.db.query(ContradictionModel)
            .filter(
                ContradictionModel.person_id == person_id,
                ContradictionModel.resolution_status == "unresolved",
            )
            .all()
        )

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------

    def complete(
        self,
        follow_up_id: str,
        outcome: str,
        caregiver_id: Optional[str] = None,
    ) -> FollowUp:
        follow_up = self.db.query(FollowUp).filter(FollowUp.id == follow_up_id).first()
        if follow_up is None:
            raise ValueError("Follow-up not found")
        if follow_up.status != FollowUpStatus.SCHEDULED:
            raise ValueError(f"Follow-up is already {follow_up.status.value}")
        follow_up.status = FollowUpStatus.COMPLETED
        follow_up.completed_at = utc_now()
        follow_up.outcome = outcome.strip()
        follow_up.completed_by_caregiver_id = caregiver_id
        self.db.flush()
        return follow_up

    def reschedule(
        self, follow_up_id: str, due_at: Any, why: Optional[str] = None
    ) -> FollowUp:
        follow_up = self.db.query(FollowUp).filter(FollowUp.id == follow_up_id).first()
        if follow_up is None:
            raise ValueError("Follow-up not found")
        if follow_up.status == FollowUpStatus.COMPLETED:
            raise ValueError("A completed follow-up cannot be rescheduled; schedule a new one")
        moment = ensure_utc(parse_time(due_at))
        if moment is None:
            raise ValueError("A new due date is required")
        follow_up.due_at = moment
        if why:
            follow_up.why_open = f"{follow_up.why_open or ''} Rescheduled: {why}".strip()
        self.db.flush()
        return follow_up

    def cancel(self, follow_up_id: str, reason: str) -> FollowUp:
        follow_up = self.db.query(FollowUp).filter(FollowUp.id == follow_up_id).first()
        if follow_up is None:
            raise ValueError("Follow-up not found")
        follow_up.status = FollowUpStatus.CANCELLED
        follow_up.outcome = reason.strip()
        self.db.flush()
        return follow_up

    def schedule_view(self, person_id: str, as_of: Optional[Any] = None) -> Dict[str, Any]:
        now = ensure_utc(parse_time(as_of)) or utc_now()
        scheduled = self.list_for_person(person_id, status=FollowUpStatus.SCHEDULED)
        return {
            "person_id": person_id,
            "as_of": now,
            "scheduled": [
                {
                    "id": f.id,
                    "what_to_recheck": f.what_to_recheck,
                    "why_open": f.why_open,
                    "due_at": f.due_at,
                    "interval_days": f.interval_days,
                    "trigger": f.trigger.value if hasattr(f.trigger, "value") else f.trigger,
                    "days_until_due": days_between(now, ensure_utc(f.due_at)),
                    "what_result_changes_the_plan": f.what_result_changes_the_plan,
                }
                for f in scheduled
            ],
            "overdue": [f.id for f in self.overdue(person_id, now)],
            "completed_count": len(self.list_for_person(person_id, status=FollowUpStatus.COMPLETED)),
        }


def _default_interval(trigger: Any) -> int:
    for key, value in _DEFAULT_INTERVAL.items():
        if trigger == key:
            return value
    return 14


def _interval_from_confidence(confidence: Optional[float], default: int) -> int:
    """Less certain readings mean an earlier re-check.

    Three readings that happen to agree still do not establish a decline, so
    the window is short enough to go and look again.
    """
    if confidence is None:
        return max(3, default // 2)
    if confidence >= 0.8:
        return default
    if confidence >= 0.5:
        return max(3, int(default * 0.7))
    return max(3, int(default * 0.4))


def _trajectories_view(engine: FollowUpEngine, person_id: str) -> List[Dict[str, Any]]:
    rows = (
        engine.db.query(Trajectory)
        .filter(Trajectory.person_id == person_id, Trajectory.is_active.is_(True))
        .limit(10)
        .all()
    )
    return [
        {
            "subject": t.subject,
            "direction": t.direction.value if hasattr(t.direction, "value") else t.direction,
            "point_count": t.point_count,
            "confidence": t.confidence,
        }
        for t in rows
    ]


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


def _enum_or_value(value: Any, enum_cls) -> Any:
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    raw = value.value if hasattr(value, "value") else str(value)
    try:
        return enum_cls(raw)
    except ValueError:
        fallback = getattr(enum_cls, "UNCERTAINTY", None) or list(enum_cls)[0]
        return fallback
