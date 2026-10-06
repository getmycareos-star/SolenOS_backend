"""Caregiver wellbeing.

The caregiver is the user of this system as much as the person is, and this is
the only part of it that is about them.

Three rules run through the whole module.

First: nothing here is evidence about the person being cared for. No wellbeing
check-in creates an Evidence row, a CareEvent, or anything the change detector
or the safety engine reads. A daughter logging four nights of broken sleep is
describing herself, and the record of her mother has no room for that.

Second: no clinical claims. A four-point scale recorded twice does not establish
burnout, depression, or anything else, and this module never asserts one. What
it can honestly say is arithmetic about what was recorded: three of your last
four stress check-ins were 4 or 5. That is checkable, and a caregiver can
disagree with it.

Third: the caregiver's own data is not shared. A support suggestion about a
caregiver's exhaustion belongs to that caregiver. It is not visible to the rest
of the care circle by default, because being visibly the struggling one is
exactly the kind of exposure a person is not expecting when they answer a
question about their own night.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.wellbeing_enums import (
    SupportKind,
    WellbeingAxis,
    WellbeingSignal,
    WELLBEING_POLES,
    direction_for,
)
from app.models.wellbeing import SupportAction, WellbeingCheckIn, WellbeingObservation


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes even from timezone-aware columns.

    Comparing those to an aware "now" raises, and a crash in the wellbeing path
    would take the caregiver's whole check-in down with it. Naive values are
    read as UTC, which is what they were written as.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class WellbeingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Check-ins
    # ------------------------------------------------------------------

    def record_check_in(
        self,
        caregiver_id: str,
        axis: str,
        score: int,
        note: Optional[str] = None,
        checked_in_at: Optional[datetime] = None,
    ) -> WellbeingCheckIn:
        """Record what a caregiver says about themselves.

        The whole point is that this touches only the caregiver's own table. It
        deliberately does not call into the evidence or event pipeline, because
        that pipeline belongs to the person being cared for.
        """
        axis_value = WellbeingAxis(axis)
        if not 1 <= int(score) <= 5:
            raise ValueError("score must be between 1 and 5")

        low_score, low_label, high_score, high_label = WELLBEING_POLES[axis_value]

        row = WellbeingCheckIn(
            caregiver_id=caregiver_id,
            axis=axis_value,
            score=int(score),
            direction=direction_for(axis_value),
            low_label=low_label,
            high_label=high_label,
            # The caregiver's words, unchanged. Nothing here rewrites them.
            note=note or None,
            checked_in_at=_as_aware(checked_in_at) or _utc_now(),
        )
        self.db.add(row)
        self.db.flush()
        return row

    def recent_check_ins(
        self, caregiver_id: str, axis: Optional[WellbeingAxis] = None, limit: int = 30
    ) -> List[WellbeingCheckIn]:
        query = self.db.query(WellbeingCheckIn).filter(
            WellbeingCheckIn.caregiver_id == caregiver_id
        )
        if axis is not None:
            query = query.filter(WellbeingCheckIn.axis == axis)
        return (
            query.order_by(WellbeingCheckIn.checked_in_at.desc()).limit(limit).all()
        )

    def history(
        self,
        caregiver_id: str,
        days: int = 30,
        axis: Optional[WellbeingAxis] = None,
    ) -> List[WellbeingCheckIn]:
        cutoff = _utc_now() - timedelta(days=days)
        rows = self.recent_check_ins(caregiver_id, axis=axis, limit=500)
        return [r for r in rows if _as_aware(r.checked_in_at) >= cutoff]

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def active_observations(self, caregiver_id: str) -> List[WellbeingObservation]:
        """Observations a caregiver has not dismissed.

        Dismissal is permanent. A system that keeps re-raising something you
        told it to drop is not listening, and for a tired person it is one more
        thing to have to deal with.
        """
        return (
            self.db.query(WellbeingObservation)
            .filter(
                WellbeingObservation.caregiver_id == caregiver_id,
                WellbeingObservation.is_dismissed.is_(False),
            )
            .order_by(WellbeingObservation.observed_at.desc())
            .all()
        )

    def dismiss(self, observation_id: str, note: Optional[str] = None) -> WellbeingObservation:
        observation = (
            self.db.query(WellbeingObservation)
            .filter(WellbeingObservation.id == observation_id)
            .first()
        )
        if observation is None:
            raise ValueError("no such wellbeing observation")
        observation.is_dismissed = True
        observation.dismissed_at = _utc_now()
        observation.dismissed_note = note or None
        self.db.flush()
        return observation

    def observe(self, caregiver_id: str, days: int = 14) -> List[WellbeingObservation]:
        """Look across recent check-ins and record what the arithmetic shows.

        Every statement produced here is a count or a comparison of recorded
        values. None of them is a conclusion about the caregiver's health, and
        each carries the check-ins it came from so it can be checked.
        """
        created: List[WellbeingObservation] = []
        existing_signals = {
            (o.signal, _as_aware(o.observed_at))
            for o in self.db.query(WellbeingObservation)
            .filter(WellbeingObservation.caregiver_id == caregiver_id)
            .all()
        }

        stress = self.history(caregiver_id, days=days, axis=WellbeingAxis.STRESS)
        if len(stress) >= 3:
            recent = stress[:4]
            high = [c for c in recent if c.score >= 4]
            if len(high) >= 3 and (WellbeingSignal.SUSTAINED_HIGH_STRESS.value, None) not in existing_signals:
                created.append(
                    self._record_observation(
                        caregiver_id=caregiver_id,
                        signal=WellbeingSignal.SUSTAINED_HIGH_STRESS,
                        statement=(
                            "You have recorded a high stress level on "
                            f"{len(high)} of your last {len(recent)} stress check-ins."
                        ),
                        basis="stress check-ins scoring 4 or 5",
                        source=recent,
                    )
                )
            elif len(high) == 1 and len(recent) >= 3:
                # An improvement is worth saying out loud. Only ever raised when
                # the caregiver is recording at all, and never as reassurance
                # about their mother's situation.
                pass

        sleep = self.history(caregiver_id, days=days, axis=WellbeingAxis.SLEEP)
        if len(sleep) >= 3:
            recent = sleep[:3]
            poor = [c for c in recent if c.score <= 2]
            if len(poor) >= 2:
                created.append(
                    self._record_observation(
                        caregiver_id=caregiver_id,
                        signal=WellbeingSignal.SUSTAINED_POOR_SLEEP,
                        statement=(
                            f"You have recorded poor sleep on {len(poor)} of your "
                            f"last {len(recent)} sleep check-ins."
                        ),
                        basis="sleep check-ins scoring 1 or 2",
                        source=recent,
                    )
                )

        time_for_self = self.history(caregiver_id, days=days, axis=WellbeingAxis.TIME_FOR_SELF)
        if len(time_for_self) >= 2:
            recent = time_for_self[:3]
            none = [c for c in recent if c.score <= 2]
            if len(none) == len(recent) and len(recent) >= 3:
                created.append(
                    self._record_observation(
                        caregiver_id=caregiver_id,
                        signal=WellbeingSignal.LONG_WITHOUT_TIME_FOR_SELF,
                        statement=(
                            "You have not recorded any time for yourself in your "
                            f"last {len(recent)} check-ins."
                        ),
                        basis="time-for-self check-ins scoring 1 or 2",
                        source=recent,
                    )
                )

        for observation in created:
            self.db.flush()
        return created

    def _record_observation(
        self,
        caregiver_id: str,
        signal: WellbeingSignal,
        statement: str,
        basis: str,
        source: List[WellbeingCheckIn],
    ) -> WellbeingObservation:
        import json

        observation = WellbeingObservation(
            caregiver_id=caregiver_id,
            signal=signal.value,
            statement=statement,
            basis=basis,
            # Stored as text because the column is Text, matching how
            # Evidence.extra_metadata is handled elsewhere in this codebase.
            source_check_in_ids=json.dumps([c.id for c in source]),
            observed_at=_utc_now(),
        )
        self.db.add(observation)
        return observation

    # ------------------------------------------------------------------
    # Support
    # ------------------------------------------------------------------

    def offer_support(self, caregiver_id: str) -> List[SupportAction]:
        """Offer concrete, logistically actionable steps for what was observed.

        Nothing here is clinical. Every suggestion is something a person can do
        about a rota, and each one is offered at most once.
        """
        offered: List[SupportAction] = []
        existing = {
            (a.caregiver_id, a.kind)
            for a in self.db.query(SupportAction)
            .filter(SupportAction.caregiver_id == caregiver_id)
            .all()
        }
        observations = {o.signal for o in self.active_observations(caregiver_id)}

        def offer(kind: SupportKind, description: str) -> None:
            if (caregiver_id, kind.value) in existing:
                return
            action = SupportAction(
                caregiver_id=caregiver_id,
                kind=kind.value,
                description=description,
                offered_at=_utc_now(),
            )
            self.db.add(action)
            offered.append(action)

        if WellbeingSignal.SUSTAINED_HIGH_STRESS.value in observations or (
            WellbeingSignal.SUSTAINED_POOR_SLEEP.value in observations
        ):
            offer(
                SupportKind.SHARE_THE_ROTATION,
                "If there is anyone who could take one day a week, this is the point "
                "at which asking is cheapest for them.",
            )
            offer(
                SupportKind.TALK_TO_SOMEONE,
                "Some caregivers find it easier to say \"this week was hard\" than "
                "\"I am not coping\". Both are true and only the first is easy to start with.",
            )

        if WellbeingSignal.LONG_WITHOUT_TIME_FOR_SELF.value in observations:
            offer(
                SupportKind.ASK_SOMEONE_FOR_A_SHIFT,
                "One specific block of a few hours counts. It does not have to be a whole day.",
            )

        if WellbeingSignal.SUSTAINED_HIGH_STRESS.value in observations:
            offer(
                SupportKind.GP_APPOINTMENT,
                "If your own sleep has not improved in a few weeks, that is worth a "
                "mention to your own doctor. It is a normal thing to bring up.",
            )

        for action in offered:
            self.db.flush()
        return offered

    def mark_support_taken(self, action_id: str) -> SupportAction:
        action = (
            self.db.query(SupportAction).filter(SupportAction.id == action_id).first()
        )
        if action is None:
            raise ValueError("no such support action")
        action.is_taken = True
        action.taken_at = _utc_now()
        self.db.flush()
        return action

    def open_support(self, caregiver_id: str) -> List[SupportAction]:
        return (
            self.db.query(SupportAction)
            .filter(
                SupportAction.caregiver_id == caregiver_id,
                SupportAction.is_taken.is_(False),
            )
            .order_by(SupportAction.offered_at.desc())
            .all()
        )

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------

    def view(self, caregiver_id: str, days: int = 30) -> Dict[str, Any]:
        """The caregiver's own view of themselves.

        Deliberately scoped by caregiver id, with no person_id parameter anywhere.
        There is no code path by which this data can be requested for someone
        else, because the record is not about them and must not be readable as
        though it were.
        """
        history = self.history(caregiver_id, days=days)

        by_axis: Dict[str, List[Dict[str, Any]]] = {}
        for row in history:
            by_axis.setdefault(row.axis.value, []).append(
                {
                    "id": row.id,
                    "score": row.score,
                    "direction": _enum_value(row.direction),
                    "note": row.note,
                    "checked_in_at": _as_aware(row.checked_in_at).isoformat(),
                    "low_label": row.low_label,
                    "high_label": row.high_label,
                }
            )

        # The latest value per axis, with the pole labels, which is the only
        # form in which a single number is safe to show on its own.
        latest: Dict[str, Any] = {}
        for axis, rows in by_axis.items():
            rows.sort(key=lambda r: r["checked_in_at"], reverse=True)
            latest[axis] = rows[0]

        return {
            "caregiver_id": caregiver_id,
            "window_days": days,
            "check_in_count": len(history),
            "axes": latest,
            "history": by_axis,
            "observations": [
                {
                    "id": o.id,
                    "signal": o.signal,
                    "statement": o.statement,
                    "basis": o.basis,
                    "observed_at": _as_aware(o.observed_at).isoformat(),
                }
                for o in self.active_observations(caregiver_id)
            ],
            "support": [
                {
                    "id": a.id,
                    "kind": a.kind,
                    "description": a.description,
                    "offered_at": _as_aware(a.offered_at).isoformat(),
                }
                for a in self.open_support(caregiver_id)
            ],
            "_scope": (
                "This is about you, not about the person you care for. It is not "
                "part of their record and is not shared with anyone else."
            ),
        }


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)
