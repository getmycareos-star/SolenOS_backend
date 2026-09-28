"""What is changing, as distinct from what changed.

The repo already has change detection: discrete deltas, "a medication was
added", "an appointment was scheduled". That is the right tool for the question
it answers, and it is not the question here.

A trajectory is the shape of a tracked measure over time. It distinguishes a
steady drift from a step change from ordinary night-to-night noise, and it
resists the two errors that matter most: calling a single bad night a decline,
and calling real decline the person's normal variation.

The direction and the slope are computed deterministically from the readings.
The model is asked to interpret the shape and cannot change the direction, and
a model answer that disagrees with the arithmetic is recorded as a
qualification rather than overwriting it.

Point count is carried on every trajectory. Three readings do not make a trend,
and the record says how many there were so nobody has to guess.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.core.care_context_enums import TrajectoryDirection
from app.core.llm_enums import LLMReasoningMode, LLMTask
from app.models.llm import LLMReasoningResult
from app.models.trajectory import Trajectory, TrajectoryPoint
from app.services.care_common import (
    days_between,
    ensure_utc,
    evidence_map,
    mean,
    parse_time,
    stdev,
    unique,
    utc_now,
)
from app.services.llm import LocalReasoningEngine

# Below this many readings, no direction is claimed at all. Not a judgement
# about the person - just the point at which a shape exists to be described.
_MIN_POINTS_FOR_DIRECTION = 3
_MIN_POINTS_FOR_SLOPE = 4
# A move has to clear both of these to count as a direction rather than scatter:
# an absolute floor, and the noise the readings already show. A 5% weight loss
# is not nothing, so the floor is deliberately low and the scatter does most of
# the work.
_MIN_RELATIVE_MOVE = 0.02


class TrajectoryEngine:
    def __init__(self, db: Session, engine: Optional[LocalReasoningEngine] = None) -> None:
        self.db = db
        self.engine = engine or LocalReasoningEngine(db)

    # ------------------------------------------------------------------
    # Points
    # ------------------------------------------------------------------

    def record_point(
        self,
        person_id: str,
        subject: str,
        observed_at: Optional[Any] = None,
        value: Optional[float] = None,
        value_text: Optional[str] = None,
        evidence_ids: Optional[Sequence[str]] = None,
        source_text: Optional[str] = None,
        observed_by_caregiver_id: Optional[str] = None,
        is_estimate: bool = False,
        flush: bool = True,
    ) -> TrajectoryPoint:
        """Record one reading.

        A qualitative reading ("slept badly", "ate most of it") is recorded with
        value_text and no value. It still counts as a point of evidence about
        the subject, but it is not fed into arithmetic.
        """
        subject = (subject or "").strip().lower()
        if not subject:
            raise ValueError("A trajectory point needs a subject")
        if value is None and not (value_text or "").strip():
            raise ValueError("A trajectory point needs a value or a value_text")

        point = TrajectoryPoint(
            person_id=person_id,
            subject=subject,
            value=value,
            value_text=(value_text or "").strip() or None,
            observed_at=ensure_utc(parse_time(observed_at)) or utc_now(),
            observed_by_caregiver_id=observed_by_caregiver_id,
            evidence_ids=list(evidence_ids) if evidence_ids else None,
            source_text=source_text,
            is_estimate=is_estimate,
        )
        self.db.add(point)
        self.db.flush()
        return point

    def list_points(
        self, person_id: str, subject: str, limit: int = 60
    ) -> List[TrajectoryPoint]:
        return (
            self.db.query(TrajectoryPoint)
            .filter(
                TrajectoryPoint.person_id == person_id,
                TrajectoryPoint.subject == (subject or "").strip().lower(),
            )
            .order_by(TrajectoryPoint.observed_at.asc())
            .limit(limit)
            .all()
        )

    def get_trajectory(self, person_id: str, subject: str) -> Optional[Trajectory]:
        return (
            self.db.query(Trajectory)
            .filter(
                Trajectory.person_id == person_id,
                Trajectory.subject == (subject or "").strip().lower(),
                Trajectory.is_active.is_(True),
            )
            .order_by(Trajectory.created_at.desc())
            .first()
        )

    def list_trajectories(self, person_id: str) -> List[Trajectory]:
        return (
            self.db.query(Trajectory)
            .filter(Trajectory.person_id == person_id, Trajectory.is_active.is_(True))
            .order_by(Trajectory.updated_at.desc().nullslast())
            .all()
        )

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def track(
        self,
        person_id: str,
        subject: str,
        label: Optional[str] = None,
        unit: Optional[str] = None,
        baseline_variation: Optional[float] = None,
        higher_is_worse: bool = False,
    ) -> Trajectory:
        """Recompute the shape of a subject from its readings.

        The deterministic computation runs first and unconditionally. The model's
        interpretation is attached to it afterwards and cannot override it.

        higher_is_worse states whether a rise in the measure is bad news. It
        defaults to False, which is right for weight, appetite, sleep and
        activity, and wrong for pain, agitation, falls and wandering, so callers
        tracking those must say so.
        """
        subject = (subject or "").strip().lower()
        points = self.list_points(person_id, subject)
        numeric = [p for p in points if p.value is not None]
        if not numeric:
            raise ValueError(
                f"No numeric readings recorded for '{subject}'. "
                "Qualitative notes are kept as points but do not establish a shape."
            )

        direction, slope, spread, volatile, within_baseline = _compute_shape(
            numeric, baseline_variation, higher_is_worse
        )
        evidence_ids = unique([eid for p in points for eid in (p.evidence_ids or [])])
        texts = evidence_map(self.db, evidence_ids)

        readings = _readings_view(numeric)
        interpretation = self._interpret(
            person_id=person_id,
            subject=subject,
            direction=direction,
            slope=slope,
            spread=spread,
            volatile=volatile,
            within_baseline=within_baseline,
            readings=readings,
            evidence_ids=evidence_ids,
            texts=texts,
        )

        existing = self.get_trajectory(person_id, subject)
        trajectory = Trajectory(
            person_id=person_id,
            subject=subject,
            label=label or subject.replace("_", " ").title(),
            unit=unit or _infer_unit(numeric),
            direction=direction,
            higher_is_worse=higher_is_worse,
            slope_per_week=slope,
            spread=round(spread, 3) if spread is not None else None,
            point_count=len(numeric),
            window_start=ensure_utc(numeric[0].observed_at),
            window_end=ensure_utc(numeric[-1].observed_at),
            is_volatile=volatile,
            plain_reading=interpretation.get("plain_reading"),
            what_it_could_mean=interpretation.get("what_it_could_mean"),
            if_it_continues=interpretation.get("if_it_continues"),
            worth_watching_for=interpretation.get("worth_watching_for"),
            baseline_variation_explains=within_baseline,
            evidence_ids=evidence_ids,
            possible_context=interpretation.get("possible_context"),
            confidence=interpretation.get("confidence"),
            reasoning_result_id=interpretation.get("result_id"),
        )
        self.db.add(trajectory)
        self.db.flush()

        for point in points:
            point.trajectory_id = trajectory.id

        if existing is not None:
            existing.is_active = False
            existing.superseded_by_trajectory_id = trajectory.id
        self.db.flush()
        return trajectory

    def _interpret(
        self,
        person_id: str,
        subject: str,
        direction: TrajectoryDirection,
        slope: Optional[float],
        spread: Optional[float],
        volatile: bool,
        within_baseline: Optional[bool],
        readings: List[Dict[str, Any]],
        evidence_ids: Sequence[str],
        texts: Dict[str, str],
    ) -> Dict[str, Any]:
        deterministic = _deterministic_reading(direction, slope, spread, readings, within_baseline, volatile)
        result: Optional[LLMReasoningResult] = self.engine.reason(
            task=LLMTask.TRAJECTORY_INTERPRETATION,
            person_id=person_id,
            subject_type="trajectory",
            subject_id=subject,
            payload={
                "subject": subject,
                "computed_direction": direction.value,
                "computed_slope_per_week": slope,
                "spread": spread,
                "is_volatile": volatile,
                "point_count": len(readings),
                "baseline_variation_explains": within_baseline,
                "readings": readings[-14:],
                "evidence": texts,
            },
            evidence_ids=evidence_ids,
            evidence_texts=texts,
            mode=LLMReasoningMode.LLM_ASSISTED,
        )
        if result is None:
            return deterministic

        detail = _detail_map(result)
        plain = detail.get("plain_reading") or deterministic["plain_reading"]
        if direction == TrajectoryDirection.DECLINING and "improv" in str(plain).lower():
            # The model contradicting the arithmetic is worth keeping, but not
            # worth believing. The computed direction stands.
            plain = deterministic["plain_reading"]
        return {
            "plain_reading": plain,
            "what_it_could_mean": detail.get("what_it_could_mean")
            if isinstance(detail.get("what_it_could_mean"), list)
            else deterministic["what_it_could_mean"],
            "if_it_continues": detail.get("if_it_continues") or deterministic["if_it_continues"],
            "worth_watching_for": detail.get("worth_watching_for")
            or deterministic["worth_watching_for"],
            "possible_context": result.possible_context or deterministic["possible_context"],
            "confidence": result.confidence,
            "result_id": result.id,
        }

    def compare_to_baseline(
        self,
        person_id: str,
        subject: str,
        baseline_variation: Optional[float] = None,
        higher_is_worse: bool = False,
    ) -> Dict[str, Any]:
        """Recompute with a known baseline variation for this subject.

        Supplying the person's own usual spread is what turns "declining" into
        "declining beyond their normal variation", which is a different and much
        more actionable statement.
        """
        trajectory = self.track(
            person_id,
            subject,
            baseline_variation=baseline_variation,
            higher_is_worse=higher_is_worse,
        )
        return {
            "trajectory_id": trajectory.id,
            "subject": trajectory.subject,
            "direction": _enum_value(trajectory.direction),
            "higher_is_worse": trajectory.higher_is_worse,
            "point_count": trajectory.point_count,
            "slope_per_week": trajectory.slope_per_week,
            "spread": trajectory.spread,
            "baseline_variation_explains": trajectory.baseline_variation_explains,
            "window_days": days_between(
                ensure_utc(trajectory.window_start), ensure_utc(trajectory.window_end)
            ),
            "possible_context": trajectory.possible_context,
        }

    def trajectory_view(self, person_id: str) -> Dict[str, Any]:
        rows = self.list_trajectories(person_id)
        return {
            "person_id": person_id,
            "trajectories": [
                {
                    "id": t.id,
                    "subject": t.subject,
                    "label": t.label,
                    "unit": t.unit,
                    "direction": _enum_value(t.direction),
                    "point_count": t.point_count,
                    "slope_per_week": t.slope_per_week,
                    "spread": t.spread,
                    "is_volatile": t.is_volatile,
                    "baseline_variation_explains": t.baseline_variation_explains,
                    "plain_reading": t.plain_reading,
                    "if_it_continues": t.if_it_continues,
                    "worth_watching_for": t.worth_watching_for,
                    "possible_context": t.possible_context,
                    "confidence": t.confidence,
                }
                for t in rows
            ],
            "not_enough_data": [
                subject
                for subject in _subjects_with_points(self.db, person_id)
                if not any(t.subject == subject for t in rows)
            ],
        }


def _compute_shape(
    points: Sequence[TrajectoryPoint],
    baseline_variation: Optional[float],
    higher_is_worse: bool = False,
) -> Tuple[TrajectoryDirection, Optional[float], Optional[float], bool, Optional[bool]]:
    """Direction, slope and volatility from the readings alone.

    A direction is only claimed when the move is larger than both an absolute
    floor and the scatter already present in the readings. Whether a rise is
    good news comes from higher_is_worse, never from an assumption about the
    measure, because weight rising and pain rising are opposite events and only
    the caller knows which one they are tracking.
    """
    values = [p.value for p in points if p.value is not None]
    if len(values) < _MIN_POINTS_FOR_DIRECTION:
        return TrajectoryDirection.INSUFFICIENT_DATA, None, _safe_stdev(values), False, None

    spread = _safe_stdev(values)
    first_half = values[: max(1, len(values) // 2)]
    second_half = values[max(1, len(values) // 2) :] or values
    early_mean = mean(first_half)
    late_mean = mean(second_half)

    volatility_threshold = (spread or 0.0) * 1.5
    volatile = bool(
        spread
        and spread > 0
        and early_mean is not None
        and late_mean is not None
        and abs(late_mean - early_mean) < volatility_threshold * 0.5
    )

    within_baseline: Optional[bool] = None
    if baseline_variation is not None and baseline_variation > 0 and spread is not None:
        within_baseline = abs((late_mean or 0) - (early_mean or 0)) <= baseline_variation

    if early_mean in (None, 0) or late_mean is None:
        return TrajectoryDirection.INSUFFICIENT_DATA, None, spread, volatile, within_baseline

    relative_move = (late_mean - early_mean) / abs(early_mean)
    slope = _slope_per_week(points)

    if within_baseline:
        # Movement that the person's own normal spread already explains is not
        # a decline. Saying so is the single most useful thing this engine does.
        return TrajectoryDirection.STABLE, slope, spread, volatile, True
    if volatile:
        return TrajectoryDirection.VOLATILE, slope, spread, True, within_baseline

    # The move must clear the noise as well as the floor, so ordinary scatter
    # does not get reported as a trend.
    noise = (spread or 0.0) / abs(early_mean)
    threshold = max(_MIN_RELATIVE_MOVE, noise)
    if abs(relative_move) < threshold:
        return TrajectoryDirection.STABLE, slope, spread, False, within_baseline

    worsening = relative_move > 0 if higher_is_worse else relative_move < 0
    return (
        TrajectoryDirection.DECLINING if worsening else TrajectoryDirection.IMPROVING,
        slope,
        spread,
        False,
        within_baseline,
    )


def _slope_per_week(points: Sequence[TrajectoryPoint]) -> Optional[float]:
    """Least-squares slope, expressed per week.

    Uses elapsed time rather than reading index, because readings arrive
    irregularly and indexing would treat a three-month gap as one step.
    """
    pairs = []
    origin = ensure_utc(points[0].observed_at)
    if origin is None:
        return None
    for point in points:
        moment = ensure_utc(point.observed_at)
        if moment is None or point.value is None:
            continue
        weeks = (moment - origin).total_seconds() / (7 * 24 * 3600)
        pairs.append((weeks, point.value))

    if len(pairs) < _MIN_POINTS_FOR_SLOPE:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return None
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    if math.isnan(numerator) or math.isinf(numerator):
        return None
    return round(numerator / denominator, 4)


def _deterministic_reading(
    direction: TrajectoryDirection,
    slope: Optional[float],
    spread: Optional[float],
    readings: Sequence[Dict[str, Any]],
    within_baseline: Optional[bool],
    volatile: bool,
) -> Dict[str, Any]:
    count = len(readings)
    if direction == TrajectoryDirection.INSUFFICIENT_DATA:
        return {
            "plain_reading": (
                f"{count} reading(s) recorded. That is not enough to call a direction in either way."
            ),
            "what_it_could_mean": [],
            "if_it_continues": None,
            "worth_watching_for": "Enough further readings to see whether there is a shape at all.",
            "possible_context": (
                "With this few readings, any direction would be a guess. The honest reading is that it is "
                "not yet known."
            ),
            "confidence": None,
        }

    unit_line = ""
    if slope is not None:
        unit_line = f" Changing by roughly {abs(slope):.4g} per week."

    if within_baseline:
        reading = (
            f"The direction of travel is {direction.value}, but the amount of movement sits within this "
            f"person's own normal variation. On {count} readings that is better described as their usual "
            f"than as a change.{unit_line}"
        )
    else:
        reading = (
            f"{count} readings over {days_between(_reading_date(readings[0]), _reading_date(readings[-1])) or '?'} "
            f"days show a {direction.value} direction.{unit_line}"
        )
        if volatile:
            reading += " The readings also vary a lot, so the direction is less firm than the count suggests."

    return {
        "plain_reading": reading,
        "what_it_could_mean": [],
        "if_it_continues": None,
        "worth_watching_for": "The next few readings will show whether the direction holds.",
        "possible_context": (
            "A direction over this many readings does not establish why. Several things could produce the "
            "same shape, and nothing here distinguishes between them."
        ),
        "confidence": None,
    }


def _readings_view(points: Sequence[TrajectoryPoint]) -> List[Dict[str, Any]]:
    return [
        {
            "value": point.value,
            "value_text": point.value_text,
            "observed_at": ensure_utc(point.observed_at).isoformat()
            if ensure_utc(point.observed_at)
            else None,
            "is_estimate": point.is_estimate,
            "evidence_ids": point.evidence_ids or [],
        }
        for point in points
    ]


def _reading_date(reading: Dict[str, Any]) -> Optional[datetime]:
    return parse_time(reading.get("observed_at"))


def _safe_stdev(values: Sequence[float]) -> Optional[float]:
    result = stdev(values)
    if result is None or math.isnan(result) or math.isinf(result):
        return None
    return result


def _infer_unit(points: Sequence[TrajectoryPoint]) -> Optional[str]:
    return None


def _subjects_with_points(db: Session, person_id: str) -> List[str]:
    rows = (
        db.query(TrajectoryPoint.subject)
        .filter(TrajectoryPoint.person_id == person_id)
        .distinct()
        .all()
    )
    return [row[0] for row in rows]


def _detail_map(result: LLMReasoningResult) -> Dict[str, Any]:
    detail: Dict[str, Any] = {}
    for item in result.detail or []:
        if isinstance(item, dict) and "field" in item:
            detail[item["field"]] = item.get("value")
    return detail


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)
