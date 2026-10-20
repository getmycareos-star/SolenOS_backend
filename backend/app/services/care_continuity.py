"""Care continuity.

This is the through-line rather than a layer. Every other module answers a
question about the present; this one is the only place that answers "how did we
get here, and what does the next person need to know".

The three things it must never lose across a change of caregiver, a change of
setting, or a quiet fortnight:

  what was known before
  what is known now
  what will matter next

The gap is the part that is usually dropped, so it is made structural. A thread
that has gone quiet is marked stale with the date it went quiet, and a handoff
brief states the gap in days rather than smoothing over it. "Nothing recorded
since the 3rd" is the single most useful sentence in a handoff, and smoothing it
into "sleep has been unsettled" loses the thing the new caregiver needed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.care_context_enums import HandoffReason
from app.core.llm_enums import LLMReasoningMode, LLMTask
from app.models.care import CareEvent
from app.models.continuity import (
    ContinuityHandoff,
    ContinuitySnapshot,
    ContinuityThread,
)
from app.models.dementia import DementiaBaselineModel
from app.models.llm import LLMReasoningResult
from app.services.care_common import (
    days_between,
    ensure_utc,
    evidence_map,
    parse_time,
    unique,
    utc_now,
)
from app.services.llm import LocalReasoningEngine

from app.services.notification import create_notification_service
from app.schemas.notification import NotificationCreate
# A thread is stale when nothing has been recorded against it for this long. Not
# a clinical threshold: it is the point at which "this has been going on" stops
# being available to a new caregiver who has not been here.
_STALE_AFTER_DAYS = 7
_HISTORY_LIMIT = 60


class CareContinuityEngine:
    def __init__(self, db: Session, engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self.engine = engine or LocalReasoningEngine(db)

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    def touch_thread(
        self,
        person_id: str,
        subject: str,
        label: Optional[str] = None,
        observed_at: Optional[Any] = None,
    ) -> ContinuityThread:
        """Record a timestamped observation and notify only at count milestones."""
        subject = (subject or "").strip().lower()
        if not subject:
            raise ValueError("A continuity thread needs a subject")

        moment = parse_time(observed_at) or utc_now()
        thread = self.get_thread(person_id, subject)
        if thread is None:
            thread = ContinuityThread(
                person_id=person_id,
                subject=subject,
                label=label or subject.replace("_", " ").title(),
                first_recorded_at=moment,
                last_recorded_at=moment,
                observation_count=0,
            )
            self.db.add(thread)
            self.db.flush()
        else:
            if label and thread.label != label:
                thread.label = label
            first = ensure_utc(thread.first_recorded_at)
            if first is None or moment < first:
                thread.first_recorded_at = moment
            last = ensure_utc(thread.last_recorded_at)
            if last is None or moment > last:
                thread.last_recorded_at = moment
            thread.is_stale = False
            thread.stale_since = None

        thread.observation_count = (thread.observation_count or 0) + 1
        self.db.flush()

        # Notification creation must not interrupt timestamp/count maintenance.
        if thread.observation_count in [1, 5, 10, 25, 50]:
            notification_data = NotificationCreate(
                recipient_person_id=person_id,
                type="care_update",
                category="clinical",
                title="Care Activity Recorded",
                message=(
                    f"New observation recorded for {thread.subject}: "
                    f"{thread.observation_count} total observations"
                ),
                related_entity_type="continuity_thread",
                related_entity_id=str(thread.id),
                priority="low",
            )
            create_notification_service(self.db, notification_data)
        return thread

    def get_thread(self, person_id: str, subject: str) -> Optional[ContinuityThread]:
        return (
            self.db.query(ContinuityThread)
            .filter(
                ContinuityThread.person_id == person_id,
                ContinuityThread.subject == (subject or "").strip().lower(),
                ContinuityThread.is_active.is_(True),
            )
            .first()
        )

    def list_threads(self, person_id: str, include_stale: bool = True) -> List[ContinuityThread]:
        query = self.db.query(ContinuityThread).filter(
            ContinuityThread.person_id == person_id, ContinuityThread.is_active.is_(True)
        )
        if not include_stale:
            query = query.filter(ContinuityThread.is_stale.is_(False))
        return query.order_by(ContinuityThread.last_recorded_at.desc().nullslast()).all()

    def refresh_staleness(self, person_id: str, as_of: Optional[Any] = None) -> List[ContinuityThread]:
        """Mark threads that have gone quiet, and note when they went quiet.

        Called before generating a handoff so a thread that dried up two weeks
        ago says so with the date, rather than being presented as if it were live.
        """
        now = ensure_utc(parse_time(as_of)) or utc_now()
        changed: List[ContinuityThread] = []
        for thread in self.list_threads(person_id, include_stale=True):
            last = ensure_utc(thread.last_recorded_at)
            gap = days_between(last, now)
            if gap is None:
                thread.is_stale = True
                thread.stale_since = thread.stale_since or now
                changed.append(thread)
                continue
            if gap >= _STALE_AFTER_DAYS:
                if not thread.is_stale:
                    thread.stale_since = last
                thread.is_stale = True
                changed.append(thread)
            else:
                if thread.is_stale:
                    thread.is_stale = False
                    thread.stale_since = None
                    changed.append(thread)
        self.db.flush()
        # Send notification for significant thread updates (only for certain observation counts)
        if thread.observation_count in [1, 5, 10, 25, 50]:  # Notify at milestones
            notification_data = NotificationCreate(
                recipient_person_id=person_id,
                type='care_update',
                category='clinical',
                title='Care Activity Recorded',
                message=f'New observation recorded for {thread.subject}: {thread.observation_count} total observations',
                related_entity_type='continuity_thread',
                related_entity_id=thread.id,
                priority='low'
            )
            create_notification_service(self.db, notification_data)
        return changed

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def build_snapshot(
        self,
        person_id: str,
        subject: Optional[str] = None,
        as_of: Optional[Any] = None,
    ) -> ContinuitySnapshot:
        """What was known, what is known, and what will matter next."""
        now = ensure_utc(parse_time(as_of)) or utc_now()
        thread = self.get_thread(person_id, subject) if subject else None

        events = self._events_for(person_id, subject, limit=_HISTORY_LIMIT)
        evidence_ids = unique([eid for event in events for eid in (event.evidence_ids or [])])
        texts = evidence_map(self.db, evidence_ids)

        previously, currently, next_items, gaps = self._deterministic_history(person_id, events, thread, now)

        narrative: Dict[str, Any] = {}
        result: Optional[LLMReasoningResult] = self.engine.reason(
            task=LLMTask.CONTINUITY_NARRATIVE,
            person_id=person_id,
            subject_type="continuity_thread",
            subject_id=thread.id if thread else None,
            payload={
                "thread": _thread_view(thread, now),
                "recorded_events": _event_view(events[-12:]),
                "baselines": self._baseline_view(person_id),
                "evidence": texts,
            },
            evidence_ids=evidence_ids,
            evidence_texts=texts,
            mode=LLMReasoningMode.LLM_ASSISTED,
        )
        if result is not None:
            detail = _detail_map(result)
            previously = _string_list(detail.get("previously_known")) or previously
            currently = _string_list(detail.get("currently_known")) or currently
            next_items = _string_list(detail.get("what_will_matter_next")) or next_items
            gaps = detail.get("gaps") if isinstance(detail.get("gaps"), list) else gaps
            narrative = {
                "summary": result.summary,
                "possible_context": result.possible_context,
                "confidence": result.confidence,
            }
        else:
            narrative = {
                "summary": _deterministic_summary(subject, previously, currently, gaps),
                "possible_context": (
                    "This is assembled from what has been recorded. It does not include what was noticed but "
                    "never written down, which is often the most useful part of a caregiver's day."
                ),
                "confidence": None,
            }

        existing = None
        if thread is not None:
            existing = (
                self.db.query(ContinuitySnapshot)
                .filter(
                    ContinuitySnapshot.thread_id == thread.id,
                    ContinuitySnapshot.is_active.is_(True),
                )
                .order_by(ContinuitySnapshot.created_at.desc())
                .first()
            )

        snapshot = ContinuitySnapshot(
            person_id=person_id,
            subject=subject,
            thread_id=thread.id if thread else None,
            previously_known=previously,
            currently_known=currently,
            what_will_matter_next=next_items,
            gaps=gaps,
            evidence_ids=evidence_ids,
            possible_context=narrative.get("possible_context"),
            confidence=narrative.get("confidence"),
            reasoning_result_id=result.id if result is not None else None,
            care_giver_changes=self.count_caregiver_changes(person_id, since=thread.first_recorded_at if thread else None),
            setting_changes=self.count_setting_changes(person_id),
        )
        self.db.add(snapshot)
        self.db.flush()
        if existing is not None:
            existing.is_active = False
            existing.superseded_by_snapshot_id = snapshot.id
        self.db.flush()
        return snapshot

    def _events_for(
        self, person_id: str, subject: Optional[str], limit: int = _HISTORY_LIMIT
    ) -> List[CareEvent]:
        query = self.db.query(CareEvent).filter(CareEvent.person_id == person_id)
        if subject:
            lowered = subject.lower()
            query = query.filter(CareEvent.event_type == lowered)
        return (
            query.order_by(CareEvent.recorded_at.asc().nullslast())
            .limit(limit)
            .all()
        )

    def _deterministic_history(
        self,
        person_id: str,
        events: Sequence[CareEvent],
        thread: Optional[ContinuityThread],
        now: datetime,
    ) -> "tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]":
        """Derive the three-part history from the record alone.

        Everything here is computed before the model is asked and is never
        overwritten by it. The model reads this history; it does not produce it.
        """
        dated = [e for e in events if ensure_utc(e.occurred_at) is not None]
        undated = [e for e in events if ensure_utc(e.occurred_at) is None]
        undated_note = (
            {
                "statement": (
                    f"{len(undated)} recorded item(s) have no time attached, so they cannot be placed "
                    "in order. They are not being treated as recent."
                ),
                "evidence_ids": [],
            }
            if undated
            else None
        )

        midpoint = len(dated) // 2 if len(dated) >= 4 else len(dated)
        earlier = dated[:midpoint]
        later = dated[midpoint:] or dated

        previously = [
            {
                "statement": event.title,
                "at": ensure_utc(event.occurred_at).isoformat(),
                "reporter": event.created_by_caregiver_id,
                "evidence_ids": event.evidence_ids or [],
            }
            for event in earlier[-6:]
        ]
        currently = [
            {
                "statement": event.title,
                "at": ensure_utc(event.occurred_at).isoformat(),
                "reporter": event.created_by_caregiver_id,
                "evidence_ids": event.evidence_ids or [],
            }
            for event in later[-6:]
        ]
        if undated_note:
            currently.append(undated_note)

        next_items = self._what_next(person_id, thread, now)

        gaps: List[Dict[str, Any]] = []
        if thread is not None:
            last = ensure_utc(thread.last_recorded_at)
            gap_days = days_between(last, now)
            if gap_days is not None and gap_days >= _STALE_AFTER_DAYS:
                gaps.append(
                    {
                        "description": (
                            f"Nothing has been recorded on this thread for {gap_days} days."
                        ),
                        "since": last.isoformat(),
                        "risk_of_ignoring": (
                            "A new caregiver reading only the recent entries will not know this has been "
                            "going on for weeks rather than starting today."
                        ),
                    }
                )
        if not events:
            gaps.append(
                {
                    "description": "There is nothing recorded on this thread at all.",
                    "since": None,
                    "risk_of_ignoring": "Anything assumed about this area is an assumption, not a finding.",
                }
            )
        if undated:
            gaps.append(
                {
                    "description": f"{len(undated)} item(s) cannot be placed in time.",
                    "since": None,
                    "risk_of_ignoring": "Ordering errors here can put a change before its cause.",
                }
            )

        return previously, currently, next_items, gaps

    def _what_next(
        self, person_id: str, thread: Optional[ContinuityThread], now: datetime
    ) -> List[Dict[str, Any]]:
        """What is likely to need attention next, derived from open state.

        Sourced from things the record already flags as open - stale threads,
        follow-ups due, unresolved questions - rather than predicted.
        """
        from app.models.follow_up import FollowUp
        from app.models.uncertainty import OpenQuestion

        items: List[Dict[str, Any]] = []

        due = (
            self.db.query(FollowUp)
            .filter(
                FollowUp.person_id == person_id,
                FollowUp.is_active.is_(True),
                FollowUp.due_at <= now,
            )
            .order_by(FollowUp.due_at.asc())
            .limit(5)
            .all()
        )
        for follow_up in due:
            items.append(
                {
                    "statement": f"Overdue re-check: {follow_up.what_to_recheck}",
                    "why": "It was scheduled for re-checking and that has not happened.",
                    "evidence_ids": follow_up.evidence_ids or [],
                }
            )

        open_questions = (
            self.db.query(OpenQuestion)
            .filter(OpenQuestion.person_id == person_id, OpenQuestion.status == "open")
            .order_by(OpenQuestion.asked_at.asc())
            .limit(5)
            .all()
        )
        for question in open_questions:
            items.append(
                {
                    "statement": question.question,
                    "why": "Asked and never answered.",
                    "evidence_ids": question.evidence_ids or [],
                }
            )

        if thread is not None and thread.is_stale:
            items.append(
                {
                    "statement": f"Confirm whether '{thread.label}' is still going on.",
                    "why": "The thread has gone quiet, which is not evidence that it stopped.",
                    "evidence_ids": [],
                }
            )
        return items

    # ------------------------------------------------------------------
    # Handoff
    # ------------------------------------------------------------------

    def build_handoff(
        self,
        person_id: str,
        reason: Any = HandoffReason.CAREGIVER_CHANGE,
        from_caregiver_id: Optional[str] = None,
        to_caregiver_id: Optional[str] = None,
        setting_change_note: Optional[str] = None,
        as_of: Optional[Any] = None,
    ) -> ContinuityHandoff:
        """A brief for whoever is taking over.

        Built from the record first. The model is asked to write the narrative on
        top of that, and cannot change the contents of the lists.
        """
        now = ensure_utc(parse_time(as_of)) or utc_now()
        self.refresh_staleness(person_id, now)

        events = self._recent_events(person_id, limit=20)
        evidence_ids = unique([eid for event in events for eid in (event.evidence_ids or [])])
        texts = evidence_map(self.db, evidence_ids)

        changed, brief, goes_wrong, open_threads, first_day = self._deterministic_handoff(person_id, events, now)
        gap_days = self._longest_gap_days(person_id)

        summary = _deterministic_handoff_summary(changed, brief, gap_days)
        possible_context = (
            "This brief covers what has been written down. It does not cover what the outgoing caregiver "
            "knows but never recorded, which is usually where the practical detail lives. Ask them directly."
        )
        confidence: Optional[float] = None
        result: Optional[LLMReasoningResult] = None

        model_result = self.engine.reason(
            task=LLMTask.CONTINUITY_HANDOFF,
            person_id=person_id,
            subject_type="handoff",
            payload={
                "reason": _enum_value(reason),
                "from_caregiver_id": from_caregiver_id,
                "to_caregiver_id": to_caregiver_id,
                "setting_change_note": setting_change_note,
                "recent_events": _event_view(events[:10]),
                "baselines": self._baseline_view(person_id),
                "known_triggers": self._trigger_view(person_id),
                "known_calmers": self._calmer_view(person_id),
                "days_since_last_record": self._days_since_last_record(person_id, now),
                "evidence": texts,
            },
            evidence_ids=evidence_ids,
            evidence_texts=texts,
            mode=LLMReasoningMode.LLM_ASSISTED,
        )
        if model_result is not None:
            detail = _detail_map(model_result)
            result = model_result
            changed = _string_list(detail.get("whats_changed")) or changed
            goes_wrong = detail.get("what_goes_wrong") if isinstance(detail.get("what_goes_wrong"), list) else goes_wrong
            open_threads = _string_list(detail.get("open_threads")) or open_threads
            first_day = _string_list(detail.get("first_24_hours")) or first_day
            brief = detail.get("person_in_brief") or brief
            summary = model_result.summary
            possible_context = model_result.possible_context or possible_context
            confidence = model_result.confidence

        handoff = ContinuityHandoff(
            person_id=person_id,
            reason=_enum_or_value(reason, HandoffReason),
            from_caregiver_id=from_caregiver_id,
            to_caregiver_id=to_caregiver_id,
            setting_change_note=setting_change_note,
            summary=summary,
            whats_changed=changed,
            person_in_brief=brief,
            what_goes_wrong=goes_wrong,
            open_threads=open_threads,
            first_24_hours=first_day,
            covering_since=now,
            gap_days=gap_days,
            evidence_ids=evidence_ids,
            possible_context=possible_context,
            confidence=confidence,
            reasoning_result_id=result.id if result is not None else None,
        )
        self.db.add(handoff)
        self.db.flush()
        # Send notification for significant thread updates (only for certain observation counts)
        if thread.observation_count in [1, 5, 10, 25, 50]:  # Notify at milestones
            notification_data = NotificationCreate(
                recipient_person_id=person_id,
                type='care_update',
                category='clinical',
                title='Care Activity Recorded',
                message=f'New observation recorded for {thread.subject}: {thread.observation_count} total observations',
                related_entity_type='continuity_thread',
                related_entity_id=thread.id,
                priority='low'
            )
            create_notification_service(self.db, notification_data)
        return handoff

    def _deterministic_handoff(
        self, person_id: str, events: Sequence[CareEvent], now: datetime
    ) -> "tuple[List[Dict[str, Any]], str, List[Dict[str, Any]], List[Dict[str, Any]], List[str]]":
        changed = [
            {
                "statement": event.title,
                "at": ensure_utc(event.occurred_at).isoformat() if ensure_utc(event.occurred_at) else None,
                "evidence_ids": event.evidence_ids or [],
            }
            for event in list(events)[:8]
        ]

        from app.models.person_context import PersonCalmer, PersonTrigger

        triggers = (
            self.db.query(PersonTrigger)
            .filter(PersonTrigger.person_id == person_id, PersonTrigger.is_active.is_(True))
            .all()
        )
        calmers = (
            self.db.query(PersonCalmer)
            .filter(PersonCalmer.person_id == person_id, PersonCalmer.is_active.is_(True))
            .all()
        )

        goes_wrong = [
            {
                "situation": trigger.trigger,
                "what_happens": trigger.response_seen or "Not recorded what happened.",
                "what_helps": trigger.calming_measure or "Not recorded.",
            }
            for trigger in triggers
        ]

        threads = self.list_threads(person_id)
        open_threads = [
            f"'{thread.label}' has not been recorded on for {days_between(ensure_utc(thread.last_recorded_at), now) or '?'} days."
            for thread in threads
            if thread.is_stale
        ]

        brief = _person_brief(person_id, calmers, triggers)
        first_day = [
            "Read the stale threads: they are what has been going on that the recent entries do not show."
        ]
        if calmers:
            first_day.append("Start with what settles them: " + "; ".join(c.measure for c in calmers[:3]) + ".")
        if triggers:
            first_day.append("Avoid what sets them off: " + "; ".join(t.trigger for t in triggers[:3]) + ".")
        if not goes_wrong and not open_threads:
            first_day.append(
                "Nothing has been recorded about what usually goes wrong for this person. That is the "
                "first thing worth asking the outgoing caregiver."
            )
        return changed, brief, goes_wrong, open_threads, first_day

    def _recent_events(self, person_id: str, limit: int = 20) -> List[CareEvent]:
        return (
            self.db.query(CareEvent)
            .filter(CareEvent.person_id == person_id)
            .order_by(CareEvent.recorded_at.desc().nullslast())
            .limit(limit)
            .all()
        )

    # ------------------------------------------------------------------
    # Change detection across people and places
    # ------------------------------------------------------------------

    def count_caregiver_changes(
        self, person_id: str, since: Optional[Any] = None
    ) -> int:
        """How many times the person reporting changed.

        A change of reporter is the single largest discontinuity in a care
        record, because the new person has not seen what the old one saw.
        """
        since_dt = parse_time(since)
        query = self.db.query(CareEvent).filter(CareEvent.person_id == person_id)
        if since_dt is not None:
            query = query.filter(CareEvent.recorded_at >= since_dt)
        events = query.order_by(CareEvent.recorded_at.asc().nullslast()).all()
        changes = 0
        previous: Optional[str] = None
        for event in events:
            reporter = event.created_by_caregiver_id
            if previous is not None and reporter and reporter != previous:
                changes += 1
            if reporter:
                previous = reporter
        return changes

    def count_setting_changes(self, person_id: str) -> int:
        events = (
            self.db.query(CareEvent)
            .filter(CareEvent.person_id == person_id, CareEvent.location.isnot(None))
            .order_by(CareEvent.recorded_at.asc().nullslast())
            .all()
        )
        changes = 0
        previous: Optional[str] = None
        for event in events:
            if previous is not None and event.location != previous:
                changes += 1
            previous = event.location
        return changes

    def _longest_gap_days(self, person_id: str) -> Optional[int]:
        events = self._recent_events(person_id, limit=200)
        times = sorted(
            t for t in (ensure_utc(e.recorded_at) for e in events) if t is not None
        )
        if len(times) < 2:
            return None
        largest = 0
        for earlier, later in zip(times, times[1:]):
            gap = int((later - earlier).days)
            largest = max(largest, gap)
        return largest or None

    def _days_since_last_record(self, person_id: str, now: datetime) -> Optional[int]:
        events = self._recent_events(person_id, limit=1)
        if not events:
            return None
        return days_between(ensure_utc(events[0].recorded_at), now)

    def _baseline_view(self, person_id: str) -> List[Dict[str, Any]]:
        rows = (
            self.db.query(DementiaBaselineModel)
            .filter(
                DementiaBaselineModel.person_id == person_id,
                DementiaBaselineModel.is_active.is_(True),
            )
            .all()
        )
        return [
            {
                "domain": _enum_value(row.domain),
                "description": row.description,
                "severity": row.severity,
                "frequency": row.frequency,
                "last_confirmed": ensure_utc(row.last_confirmed_date).isoformat()
                if ensure_utc(row.last_confirmed_date)
                else None,
            }
            for row in rows
        ]

    def _trigger_view(self, person_id: str) -> List[Dict[str, Any]]:
        from app.services.person_model import PersonModelEngine

        return PersonModelEngine(self.db).trigger_view(person_id)

    def _calmer_view(self, person_id: str) -> List[Dict[str, Any]]:
        from app.services.person_model import PersonModelEngine

        return PersonModelEngine(self.db).calmer_view(person_id)

    # ------------------------------------------------------------------
    # Whole-person view
    # ------------------------------------------------------------------

    def continuity_brief(self, person_id: str, as_of: Optional[Any] = None) -> Dict[str, Any]:
        """Everything a new person needs in one payload."""
        now = ensure_utc(parse_time(as_of)) or utc_now()
        self.refresh_staleness(person_id, now)
        threads = self.list_threads(person_id)

        return {
            "person_id": person_id,
            "generated_at": now,
            "days_since_last_record": self._days_since_last_record(person_id, now),
            "longest_gap_days": self._longest_gap_days(person_id),
            "caregiver_changes": self.count_caregiver_changes(person_id),
            "setting_changes": self.count_setting_changes(person_id),
            "threads": [
                {
                    "id": thread.id,
                    "subject": thread.subject,
                    "label": thread.label,
                    "observation_count": thread.observation_count,
                    "first_recorded_at": thread.first_recorded_at,
                    "last_recorded_at": thread.last_recorded_at,
                    "is_stale": thread.is_stale,
                    "quiet_for_days": days_between(ensure_utc(thread.last_recorded_at), now),
                    "direction": _enum_value(thread.direction),
                }
                for thread in threads
            ],
            "stale_threads": [t.label for t in threads if t.is_stale],
            "the_one_line_that_matters": _gutter_line(threads, now),
        }


def _gutter_line(threads: Sequence[ContinuityThread], now: datetime) -> str:
    stale = [t for t in threads if t.is_stale]
    if stale:
        worst = max(
            stale,
            key=lambda t: days_between(ensure_utc(t.last_recorded_at), now) or 0,
        )
        gap = days_between(ensure_utc(worst.last_recorded_at), now)
        return (
            f"'{worst.label}' has not been written about for {gap} days. "
            "That is a gap in the record, not evidence that it stopped."
        )
    if not threads:
        return "Nothing has been recorded for this person yet."
    return "Every thread is current."


def _person_brief(
    person_id: str,
    calmers: Sequence[Any],
    triggers: Sequence[Any],
) -> str:
    if not calmers and not triggers:
        return (
            "The record does not yet describe this person. The outgoing caregiver is the fastest route to "
            "that, and it is worth five minutes before anything else."
        )
    parts = []
    if calmers:
        parts.append("Settles them: " + "; ".join(c.measure for c in calmers[:3]) + ".")
    if triggers:
        parts.append("Sets them off: " + "; ".join(t.trigger for t in triggers[:3]) + ".")
    return " ".join(parts)


def _deterministic_history_summary(
    subject: Optional[str], previously: Sequence[Any], currently: Sequence[Any], gaps: Sequence[Any]
) -> str:
    return _deterministic_summary(subject, previously, currently, gaps)


def _deterministic_summary(
    subject: Optional[str],
    previously: Sequence[Any],
    currently: Sequence[Any],
    gaps: Sequence[Any],
) -> str:
    name = subject or "this person's care"
    if gaps:
        return (
            f"{len(previously)} earlier and {len(currently)} recent entries on {name}. "
            f"{len(gaps)} gap(s) in the record - see what is still unknown."
        )
    return f"{len(previously)} earlier and {len(currently)} recent entries on {name}, with no gaps."


def _deterministic_handoff_summary(
    changed: Sequence[Any], brief: str, gap_days: Optional[int]
) -> str:
    if gap_days and gap_days >= 3:
        return (
            f"{len(changed)} recent entries. The record has a {gap_days}-day gap in it, so the recent "
            "entries do not show everything that has been happening."
        )
    return f"{len(changed)} recent entries to hand over."


def _thread_view(thread: Optional[ContinuityThread], now: datetime) -> Optional[Dict[str, Any]]:
    if thread is None:
        return None
    return {
        "subject": thread.subject,
        "label": thread.label,
        "observation_count": thread.observation_count,
        "first_recorded_at": ensure_utc(thread.first_recorded_at).isoformat()
        if ensure_utc(thread.first_recorded_at)
        else None,
        "last_recorded_at": ensure_utc(thread.last_recorded_at).isoformat()
        if ensure_utc(thread.last_recorded_at)
        else None,
        "quiet_for_days": days_between(ensure_utc(thread.last_recorded_at), now),
        "is_stale": thread.is_stale,
    }


def _event_view(events: Sequence[CareEvent]) -> List[Dict[str, Any]]:
    return [
        {
            "title": event.title,
            "description": event.description,
            "event_type": event.event_type,
            "occurred_at": ensure_utc(event.occurred_at).isoformat()
            if ensure_utc(event.occurred_at)
            else None,
            "recorded_at": ensure_utc(event.recorded_at).isoformat()
            if ensure_utc(event.recorded_at)
            else None,
            "location": event.location,
            "reporter": event.created_by_caregiver_id,
            "evidence_ids": event.evidence_ids or [],
        }
        for event in events
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
    return [str(item) for item in value if item not in (None, "", {})]


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
        return list(enum_cls)[0]




