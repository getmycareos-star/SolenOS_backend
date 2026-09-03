from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session

from app.models.care import CareEvent, Caregiver, Evidence
from app.models.change import Baseline, Change
from app.models.significance import SignificanceAssessment
from app.models.uncertainty import Contradiction
from app.core.change_enums import SignificanceVerdict, EvidenceStatus, BaselineConfidence


_FUNCTIONAL_IMPACT_KEYWORDS = (
    "couldn't get up", "could not get up", "unable to stand", "unable to walk",
    "needed help", "required assistance", "mobility", "walking", "fall",
    "fell", "fallen", "confused", "disoriented", "unsteady", "weak", "couldn't walk",
    "cannot walk", "lost balance", "slurred", "drooping", "vision",
)


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isinf(f) or math.isnan(f):
        return None
    return f


def _baseline_confidence_score(conf: BaselineConfidence) -> float:
    return {
        BaselineConfidence.HIGH: 1.0,
        BaselineConfidence.MODERATE: 0.7,
        BaselineConfidence.LOW: 0.4,
        BaselineConfidence.INSUFFICIENT: 0.2,
    }.get(conf, 0.2)


def _evidence_status_from_events(db: Session, events: List[CareEvent], person_id: str,
                                 subject_type: str, subject_id: str) -> EvidenceStatus:
    if not events:
        return EvidenceStatus.UNKNOWN

    evidence_ids = [eid for e in events for eid in (e.evidence_ids or [])]

    contradictions = []
    if evidence_ids:
        contradiction_rows = (
            db.query(Contradiction)
            .filter(Contradiction.person_id == person_id)
            .all()
        )
        evidence_set = set(evidence_ids)
        for c in contradiction_rows:
            c_ev = set(c.evidence_ids or [])
            if c_ev and (evidence_set & c_ev):
                contradictions.append(c)
            elif c.subject_type == subject_type and c.subject_id == subject_id:
                contradictions.append(c)
    if contradictions:
        return EvidenceStatus.CONTRADICTORY

    if _reports_conflict(events):
        return EvidenceStatus.CONTRADICTORY

    clinician_like = any(
        (eid in evidence_ids) and _text_has_clinician_authority(db, eid)
        for eid in evidence_ids
    )
    if clinician_like:
        return EvidenceStatus.CONFIRMED
    return EvidenceStatus.REPORTED


def _text_has_clinician_authority(db: Session, evidence_id: str) -> bool:
    ev = db.query(Evidence).filter(Evidence.id == evidence_id).first()
    if not ev or not ev.source_text:
        return False
    low = (ev.source_text or "").lower()
    return any(m in low for m in ("clinician", "physician", "nurse", "md", "rn ", "rn,", "doctor"))


_CONFLICT_POSITIVE_WORDS = ("normal", "fine", "healthy", "well", "okay", "ok",
                            "clear", "unremarkable", "without symptom", "no ")


def _reports_conflict(events: List[CareEvent]) -> bool:
    """Preserve opposing caregiver reports instead of choosing one.

    A conflict is present when, for the same subject and a short window, at least
    two distinct reporters produce observations and at least one uses a
    positive/normal indicator alongside a report that asserts the finding.
    This is not a clinical adjudication; it is a signal that the record holds
    mutually inconsistent observations and must not silently collapse them.
    """
    if len(events) < 2:
        return False
    reporters = [e.created_by_caregiver_id for e in events if e.created_by_caregiver_id]
    if len(set(reporters)) < 2:
        return False

    texts = [
        " ".join(filter(None, [(e.title or ""), (e.description or "")])).lower()
        for e in events
    ]
    has_positive = any(any(w in t for w in _CONFLICT_POSITIVE_WORDS) for t in texts)
    has_symptom = any(not any(w in t for w in _CONFLICT_POSITIVE_WORDS) for t in texts)
    return has_positive and has_symptom


def _contextual_associations(db: Session, person_id: str, events: List[CareEvent],
                             now: datetime) -> List[Dict[str, Any]]:
    if not events:
        return []
    start = min(_ensure_utc(e.occurred_at) for e in events)
    end = max(_ensure_utc(e.occurred_at) for e in events)
    window_start = (start - timedelta(days=14)) if start else None
    window_end = (end + timedelta(days=14)) if end else None
    if not window_start or not window_end:
        return []
    own_types = set(e.event_type for e in events)
    others = (
        db.query(CareEvent)
        .filter(
            CareEvent.person_id == person_id,
            CareEvent.occurred_at >= window_start,
            CareEvent.occurred_at <= window_end,
            ~CareEvent.event_type.in_(own_types),
        )
        .all()
    )
    return [
        {
            "event_type": o.event_type,
            "title": o.title,
            "occurred_at": _ensure_utc(o.occurred_at).isoformat() if o.occurred_at else None,
            "evidence_ids": o.evidence_ids or [],
        }
        for o in others
    ]


def _functional_impact(events: List[CareEvent]) -> bool:
    for e in events:
        blob = " ".join(filter(None, [e.title, e.description])).lower()
        if any(k in blob for k in _FUNCTIONAL_IMPACT_KEYWORDS):
            return True
    return False


def assess_change(
    db: Session,
    change: Change,
    baseline: Optional[Baseline],
    comparison: Dict[str, Any],
    events: List[CareEvent],
    now: datetime,
) -> SignificanceAssessment:
    now = _ensure_utc(now) or datetime.now(timezone.utc)
    person_id = change.person_id
    subject_type = change.subject_type
    subject_id = change.subject_id

    relative_change = _float(comparison.get("relative_change_percent"))
    baseline_confidence = comparison.get("baseline_confidence", "moderate")
    try:
        baseline_conf_enum = BaselineConfidence(baseline_confidence)
    except (TypeError, ValueError):
        baseline_conf_enum = BaselineConfidence.MODERATE

    baseline_event_count = (baseline.event_count if baseline else 0)
    current_count = change.current_count or 0
    evidence_ids = list(change.evidence_ids or [])
    reporters = _resolve_reporters(db, events)

    magnitude_score = 0.0
    if relative_change is not None:
        magnitude_score = min(relative_change / 100.0, 1.0)
        if math.isinf(relative_change) and current_count > 0:
            magnitude_score = 1.0
    elif change.change_type.value in ("onset",) and current_count > 0:
        magnitude_score = 1.0

    novelty = 1.0 if baseline_event_count == 0 else 0.0
    recurrence = 1.0 if current_count >= 2 else 0.0

    persistence_days = 0
    if len(events) >= 2:
        starts = [_ensure_utc(e.occurred_at) for e in events if e.occurred_at]
        if starts:
            persistence_days = max(0, int((max(starts) - min(starts)).total_seconds() // 86400))
    persistence_score = min(persistence_days / 14.0, 1.0)

    clustering = 1.0 if (len(events) >= 2 and persistence_days <= 14) else 0.0

    functional = _functional_impact(events)
    functional_score = 1.0 if functional else 0.0

    corroboration_score = min((len(set(reporters)) - 1) / 2.0, 1.0) if reporters else 0.0
    corroboration_score = min(corroboration_score + min(current_count / 4.0, 0.25), 1.0)

    associations = _contextual_associations(db, person_id, events, now)
    contextual_score = 1.0 if associations else 0.0

    evidence_confidence = (
        _baseline_confidence_score(baseline_conf_enum) * 0.55
        + 0.45 * (0.6 if reporters else 0.3)
    )
    evidence_confidence = round(min(max(evidence_confidence, 0.0), 1.0), 3)

    evidence_status = _evidence_status_from_events(db, events, person_id, subject_type, subject_id)

    dims: List[Dict[str, Any]] = [
        {"name": "magnitude", "score": round(magnitude_score, 3), "label": _magnitude_label(change, relative_change),
         "evidence_ids": [e for e in evidence_ids if e != (baseline.id if baseline else None)]},
        {"name": "persistence", "score": round(persistence_score, 3),
         "label": f"{persistence_days} days observed", "evidence_ids": [e.id for e in events]},
        {"name": "novelty", "score": round(novelty, 3),
         "label": "absent from baseline" if novelty else "present in baseline",
         "evidence_ids": [] if novelty else list(evidence_ids)},
        {"name": "recurrence", "score": round(recurrence, 3),
         "label": f"{current_count} events in window", "evidence_ids": [e.id for e in events]},
        {"name": "deviation_from_baseline", "score": round(magnitude_score, 3),
         "label": "relative to personal baseline", "evidence_ids": list(evidence_ids)},
        {"name": "clustering", "score": round(clustering, 3),
         "label": "events clustered" if clustering else "not clustered",
         "evidence_ids": [e.id for e in events]},
        {"name": "functional_consequence", "score": round(functional_score, 3),
         "label": "functional impact documented" if functional else "functional impact not documented",
         "evidence_ids": [e.id for e in events] if functional else []},
        {"name": "corroboration", "score": round(corroboration_score, 3),
         "label": f"{len(set(reporters))} reporter(s)", "evidence_ids": [e.id for e in events]},
        {"name": "contextual_relevance", "score": round(contextual_score, 3),
         "label": f"{len(associations)} associated event(s) within 14 days" if associations
         else "no associated events within 14 days",
         "evidence_ids": [a["evidence_ids"][0] for a in associations if a.get("evidence_ids")]},
        {"name": "uncertainty", "score": round(1.0 - evidence_confidence, 3),
         "label": _uncertainty_label(baseline_conf_enum, evidence_status, current_count),
         "evidence_ids": list(evidence_ids)},
    ]

    possible_context: List[str] = []
    if evidence_status == EvidenceStatus.REPORTED:
        possible_context.append("observations are caregiver-reported; no clinician verification recorded")
    if functional_score == 0.0:
        possible_context.append("functional impact not documented for this observation")
    if not associations:
        possible_context.append("no associated care events (e.g. medication change, hospitalization) within 14 days")
    else:
        possible_context.append("associated events are temporally proximate — association does not establish causation")
    if baseline_conf_enum in (BaselineConfidence.LOW, BaselineConfidence.INSUFFICIENT):
        possible_context.append("baseline is sparse/unstable; deviation should be confirmed with additional observations")
    if baseline and baseline.documentation_bias_flag:
        possible_context.append("documentation pattern changed vs baseline; apparent increase may reflect recording, not reality")

    verdict, significance_confidence = _decide_verdict(
        change, baseline_event_count, current_count, magnitude_score, recurrence,
        novelty, persistence_score, corroboration_score, contextual_score,
        functional_score, evidence_status, evidence_confidence,
    )

    attention_candidate = (
        verdict == SignificanceVerdict.MEANINGFUL_CANDIDATE
        and evidence_status != EvidenceStatus.CONTRADICTORY
        and (novelty == 1.0 or recurrence == 1.0 or magnitude_score >= 0.7 or clustering == 1.0)
        and (corroboration_score >= 0.3 or functional_score == 1.0 or persistence_score >= 0.5)
    )
    follow_up_candidate = (
        verdict == SignificanceVerdict.MEANINGFUL_CANDIDATE
        or (verdict == SignificanceVerdict.INSUFFICIENT_EVIDENCE and baseline_event_count == 0 and current_count > 0)
    )

    explanation = _build_explanation(
        change, baseline, comparison, events, reporters, evidence_status, verdict,
        significance_confidence, possible_context,
    )

    assessment = SignificanceAssessment(
        person_id=person_id,
        subject_type=subject_type,
        subject_id=subject_id,
        target_type="change",
        target_id=change.id,
        baseline_id=baseline.id if baseline else None,
        verdict=verdict,
        evidence_status=evidence_status,
        significance_confidence=round(significance_confidence, 3),
        evidence_confidence=evidence_confidence,
        dimensions=dims,
        possible_context=possible_context,
        reporters=reporters,
        attention_candidate=attention_candidate,
        follow_up_candidate=follow_up_candidate,
        explanation=explanation,
        evidence_ids=evidence_ids,
    )
    db.add(assessment)
    db.flush()

    change.significance_assessment_id = assessment.id
    change.significance_verdict = verdict
    change.attention_candidate = attention_candidate
    change.follow_up_candidate = follow_up_candidate
    return assessment


def _resolve_reporters(db: Session, events: List[CareEvent]) -> List[str]:
    seen = []
    for e in events:
        if e.created_by_caregiver_id and e.created_by_caregiver_id not in seen:
            seen.append(e.created_by_caregiver_id)
    if not seen:
        return []
    rows = dict(
        db.query(Caregiver.id, Caregiver.name)
        .filter(Caregiver.id.in_(seen))
        .all()
    )
    return [rows.get(r, r) for r in seen]


def _magnitude_label(change: Change, relative_change: Optional[float]) -> str:
    if relative_change is None:
        if change.change_type.value == "onset":
            return "new (absent -> present)"
        return "unquantified"
    if math.isinf(relative_change):
        return "infinite (absent -> present)"
    return f"{relative_change:.1f}% relative to baseline"


def _uncertainty_label(baseline_conf, evidence_status, current_count) -> str:
    if evidence_status == EvidenceStatus.CONTRADICTORY:
        return "conflicting observations exist"
    if baseline_conf in (BaselineConfidence.LOW, BaselineConfidence.INSUFFICIENT):
        return "baseline is sparse; significance uncertain"
    return "standard uncertainty"


def _decide_verdict(change, baseline_count, current_count, magnitude, recurrence,
                    novelty, persistence, corroboration, contextual, functional,
                    evidence_status, evidence_confidence):
    if current_count == 0 and baseline_count == 0:
        return SignificanceVerdict.NOT_MEANINGFUL, 0.0

    if evidence_status == EvidenceStatus.CONTRADICTORY:
        base = max(0.0, 0.5 - evidence_confidence)
        return SignificanceVerdict.INSUFFICIENT_EVIDENCE, round(base, 3)

    ctype = change.change_type.value

    if ctype == "cessation":
        strong_baseline = baseline_count >= 2
        if strong_baseline and current_count == 0:
            sig = 0.6 + 0.3 * evidence_confidence
            return SignificanceVerdict.MEANINGFUL_CANDIDATE, round(sig, 3)
        return SignificanceVerdict.NOT_MEANINGFUL, round(0.3 * evidence_confidence, 3)

    if ctype in ("onset", "increase", "recurrence"):
        is_novel = novelty == 1.0
        is_recurrent = recurrence == 1.0
        strong_signal = magnitude >= 0.5
        corroborated = corroboration >= 0.3
        contextualized = contextual == 1.0
        functional_impact = functional == 1.0
        sustained = persistence >= 0.5

        evidence_factor = 0.5 + 0.5 * evidence_confidence

        # A single unverified report of a non-functional, non-high-signal
        # observation (e.g. "seemed tired today") is insufficient evidence of
        # meaningful change. It may prompt a clarifying question, not attention.
        if (
            current_count == 1
            and not is_recurrent
            and not functional_impact
            and not corroborated
            and not contextualized
        ):
            return SignificanceVerdict.INSUFFICIENT_EVIDENCE, round(0.35 * evidence_factor, 3)

        if (is_novel or is_recurrent) and (
            strong_signal or sustained or corroborated or functional_impact or contextualized
        ):
            if is_novel and not is_recurrent and not corroborated and not sustained:
                sig = 0.55 * evidence_factor
            else:
                sig = (
                    0.6
                    + 0.25 * max(magnitude, persistence, corroboration)
                    + 0.15 * (1 if (contextualized or functional_impact) else 0)
                ) * evidence_factor
            return SignificanceVerdict.MEANINGFUL_CANDIDATE, round(min(sig, 0.9), 3)

        if strong_signal and (corroborated or sustained or functional_impact or contextualized):
            sig = 0.5 * evidence_factor
            return SignificanceVerdict.MEANINGFUL_CANDIDATE, round(sig, 3)

        if baseline_count > 0 and magnitude < 0.5 and not (is_recurrent or sustained):
            return SignificanceVerdict.NOT_MEANINGFUL, round(0.2 * evidence_confidence, 3)

    return SignificanceVerdict.INSUFFICIENT_EVIDENCE, round(0.3 * evidence_confidence, 3)


def _build_explanation(change, baseline, comparison, events, reporters,
                       evidence_status, verdict, sig_conf, possible_context):
    parts = []
    parts.append(f"{change.subject_type} {change.change_type.value} detected.")
    if baseline:
        parts.append(
            f"Compared to the baseline window "
            f"({_iso(baseline.window_start)} to {_iso(baseline.window_end)}): "
            f"baseline count {baseline.event_count}, current count {change.current_count}."
        )
    rel = comparison.get("relative_change_percent")
    if rel is not None:
        if math.isinf(rel):
            parts.append("Relative change: infinite (absent -> present).")
        else:
            parts.append(f"Relative change: {rel:.1f}%.")
    reporter_txt = f"Reported by {', '.join(reporters)}." if reporters else "No reporter attribution."
    parts.append(reporter_txt)
    parts.append(f"Evidence status: {evidence_status.value}.")
    parts.append(f"Significance verdict: {verdict.value} (significance confidence {sig_conf:.2f}).")
    if possible_context:
        parts.append("Uncertainty / unknowns: " + " | ".join(possible_context) + ".")
    parts.append("This is an interpretation of longitudinal change, not a diagnosis.")
    return " ".join(parts)


def _iso(dt: Optional[datetime]) -> str:
    if not dt:
        return "unknown"
    dt = _ensure_utc(dt)
    return dt.date().isoformat()
