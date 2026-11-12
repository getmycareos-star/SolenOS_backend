"""Persist source-linked Daily Care extraction and safe, non-authoritative observations."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.capture_analysis import CaptureAnalysis
from app.models.care_continuity import EvidenceObservation


def persist_capture_analysis(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    source_event_id: str | None,
    source_evidence_id: str | None,
    source_text: str,
    extraction_status: str,
    candidates: dict[str, Any] | None,
    execution_status: dict[str, str] | None = None,
    commit: bool = True,
) -> CaptureAnalysis:
    """Persist the source-linked extraction and review state without writing LCR facts."""
    analysis = CaptureAnalysis(
        person_id=person_id,
        caregiver_id=caregiver_id,
        source_event_id=source_event_id,
        source_evidence_id=source_evidence_id,
        source_text=source_text,
        extraction_status=extraction_status,
        candidates=candidates,
        review_history=[],
        execution_status=execution_status,
    )
    db.add(analysis)
    db.flush()
    if commit:
        db.commit()
    db.refresh(analysis)
    return analysis


_TIME_REFERENCE = re.compile(
    r"\b(last night|yesterday evening|yesterday morning|yesterday|this morning|"
    r"this afternoon|this evening|tonight|today|at breakfast|at lunch|at dinner)\b",
    re.IGNORECASE,
)
_HEDGE_REFERENCE = re.compile(
    r"\b(seemed|appeared|possibly|maybe|might|may have|could have|i think|"
    r"i believe|not sure|can't remember|cannot remember|don't remember)\b",
    re.IGNORECASE,
)


def persist_source_grounded_daily_observations(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    evidence_id: str,
    source_event_id: str,
    source_text: str,
    candidates: dict[str, Any] | None,
    reported_at,
) -> int:
    """Project exact-quote extraction into LCR observations, never clinical assertions.

    The candidate, subject and observation must be exact substrings of the
    caregiver's original report. Relative time wording and hedges are retained
    as source context; no absolute event time or clinical interpretation is
    manufactured.
    """
    if not isinstance(candidates, dict) or not isinstance(candidates.get("observations"), list):
        return 0

    created = 0
    for candidate in candidates["observations"][:12]:
        if not isinstance(candidate, dict):
            continue
        subject = candidate.get("subject")
        observation = candidate.get("observation")
        quote = candidate.get("source_quote")
        if not all(isinstance(value, str) and value.strip() for value in (subject, observation, quote)):
            continue
        if quote not in source_text or subject not in quote or observation not in quote:
            continue

        start = source_text.find(quote)
        if start < 0:
            continue
        end = start + len(quote)
        existing = db.query(EvidenceObservation.id).filter(
            EvidenceObservation.person_id == person_id,
            EvidenceObservation.evidence_id == evidence_id,
            EvidenceObservation.source_span_start == start,
            EvidenceObservation.source_span_end == end,
            EvidenceObservation.source_passage == quote,
            EvidenceObservation.value_text == observation,
            EvidenceObservation.subject_label == subject,
        ).first()
        if existing:
            continue

        hedge = _HEDGE_REFERENCE.search(observation)
        time_reference = _TIME_REFERENCE.search(observation) or _TIME_REFERENCE.search(quote)
        context_parts = [
            "Source-grounded extraction from a caregiver report; not an independently verified clinical fact."
        ]
        if time_reference:
            context_parts.append(f"Caregiver time wording: {time_reference.group(0)}.")
        db.add(EvidenceObservation(
            person_id=person_id,
            evidence_id=evidence_id,
            observation_kind="caregiver_report",
            epistemic_status="uncertain" if hedge else "reported",
            subject_label=subject[:160],
            attribute="caregiver_reported_observation",
            value_text=observation[:2000],
            context=" ".join(context_parts),
            reported_at=reported_at,
            observer_label="Caregiver",
            observer_caregiver_id=caregiver_id,
            observation_method="natural-language caregiver report",
            source_passage=quote,
            source_span_start=start,
            source_span_end=end,
            hedge_marker=hedge.group(0) if hedge else None,
            ambiguity_note=(
                "The caregiver used tentative wording. Keep this as an uncertain report; "
                "this is not a diagnosis or a confirmed specific symptom."
                if hedge else None
            ),
            extraction_method="qwen_source_grounded",
            extraction_version="daily-capture-source-span-v1",
            extraction_run_id=source_event_id,
            created_by_caregiver_id=caregiver_id,
        ))
        created += 1

    if created:
        db.flush()
    return created
