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
    r"this afternoon|this evening|tonight|today|now|at breakfast|at lunch|at dinner)\b",
    re.IGNORECASE,
)
_HEDGE_REFERENCE = re.compile(
    r"\b(seem(?:s|ed)?|appear(?:s|ed)?|possibly|maybe|might|may have|could have|"
    r"nothing unusual|nothing out of the ordinary|no unusual changes?|i think|"
    r"i believe|not sure|can't remember|cannot remember|don't remember)\b",
    re.IGNORECASE,
)

_CLAUSE_BOUNDARY = re.compile(r"\b(and|but|or|however|although|yet)\b|[,.;!?]", re.IGNORECASE)


def _source_hedge_for_observation(quote: str, observation: str):
    """Use only a hedge in the same clause as the extracted observation."""
    observation_match = re.search(re.escape(observation), quote, re.IGNORECASE)
    if observation_match is None:
        return _HEDGE_REFERENCE.search(observation)
    start, end = observation_match.span()
    eligible = []
    for marker in _HEDGE_REFERENCE.finditer(quote):
        if marker.end() <= start:
            gap = quote[marker.end():start]
        elif marker.start() >= end:
            gap = quote[end:marker.start()]
        else:
            gap = ""
        if not _CLAUSE_BOUNDARY.search(gap):
            eligible.append((abs(marker.start() - start), marker))
    return min(eligible, key=lambda item: item[0])[1] if eligible else None

def _source_time_for_observation(quote: str, observation: str):
    """Use only a time reference in the same clause as the extracted observation."""
    observation_match = re.search(re.escape(observation), quote, re.IGNORECASE)
    if observation_match is None:
        return None
    start, end = observation_match.span()
    eligible = []
    for marker in _TIME_REFERENCE.finditer(quote):
        if marker.start() >= start and marker.end() <= end:
            return marker
        if marker.end() <= start:
            gap = quote[marker.end():start]
        elif marker.start() >= end:
            gap = quote[end:marker.start()]
        else:
            gap = ""
        if not _CLAUSE_BOUNDARY.search(gap):
            eligible.append((abs(marker.start() - start), marker))
    return min(eligible, key=lambda item: item[0])[1] if eligible else None


_DAILY_CLAUSE_VERB = re.compile(
    r"\b(?:is|am|are|was|were|has|have|had|do|does|did|can|could|will|would|"
    r"should|might|may|must|slept|sleep|woke|wake|ate|eat|drank|drink|took|take|"
    r"refused|refuse|seemed|seem|felt|feel|fell|fall|walked|walk|uses|use|"
    r"needed|need|became|become|stayed|stay|rested|rest|reported|report|"
    r"noticed|notice|appeared|appear|looked|look|changed|change|started|start|"
    r"stopped|stop|missed|miss|forgot|forget|remembered|remember|"
    r"complained|complain|asked|ask|called|call|went|go|arrived|arrive)\b|"
    r"\b\w+(?:ed|ing)\b",
    re.IGNORECASE,
)
_DAILY_CLAUSE_SUBJECT = re.compile(
    r"^\s*((?:the\s+)?(?:patient|caregiver|doctor|nurse|care staff)|"
    r"(?:my\s+)?(?:mother|father)|mum|mom|dad|she|he|they|we|i|"
    r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
    re.IGNORECASE,
)


def supplement_source_grounded_daily_observations(
    source_text: str,
    candidates: dict[str, Any] | None,
) -> dict[str, Any]:
    """Recover omitted independent 'and' clauses without inventing source wording.

    The model remains useful for extraction, but it is not allowed to silently
    collapse a sentence containing multiple independently stated actions into
    one finding. Every supplemental observation is an exact source substring,
    retains its complete sentence as provenance, and inherits only an explicit
    subject already present in that sentence.
    """
    if not isinstance(candidates, dict):
        return {"observations": [], "open_questions": []}
    raw_observations = candidates.get("observations")
    observations = [
        dict(item) for item in raw_observations
        if isinstance(item, dict)
    ] if isinstance(raw_observations, list) else []
    existing_values = {
        str(item.get("observation") or "").strip().casefold()
        for item in observations
        if isinstance(item.get("observation"), str)
    }

    for match in re.finditer(r"[^.!?;\n]+(?:[.!?;]+|$)", source_text or ""):
        sentence = match.group(0).strip()
        clauses = [part.strip() for part in re.split(r"\s+and\s+", sentence, flags=re.IGNORECASE)]
        if len(clauses) < 2 or not all(_DAILY_CLAUSE_VERB.search(part) for part in clauses):
            continue

        sentence_subject = _DAILY_CLAUSE_SUBJECT.match(clauses[0])
        inherited_subject = sentence_subject.group(1).strip() if sentence_subject else None
        if not inherited_subject:
            continue

        # A whole-sentence model candidate is less useful than the distinct,
        # independently traceable clauses. Replace only that exact collapse.
        observations = [
            item for item in observations
            if not (
                str(item.get("observation") or "").strip().casefold() == sentence.casefold()
                and str(item.get("source_quote") or "").strip() == sentence
            )
        ]
        for clause in clauses:
            subject_match = _DAILY_CLAUSE_SUBJECT.match(clause)
            subject = subject_match.group(1).strip() if subject_match else inherited_subject
            observation = clause.strip()
            key = observation.casefold()
            if key in existing_values:
                continue
            if observation not in sentence or subject.casefold() not in sentence.casefold():
                continue
            observations.append({
                "subject": subject,
                "observation": observation,
                "source_quote": sentence,
                "extraction_origin": "deterministic_exact_clause_supplement",
            })
            existing_values.add(key)
            inherited_subject = subject

    result = dict(candidates)
    result["observations"] = observations[:12]
    if not isinstance(result.get("open_questions"), list):
        result["open_questions"] = []
    return result


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
        subject_match = re.search(re.escape(subject), quote, re.IGNORECASE)
        # Model extraction may normalise the first letter's casing even when
        # the quoted source is exact. Match case-insensitively, then persist
        # the original source-cased substring so provenance remains literal.
        observation_match = re.search(re.escape(observation), quote, re.IGNORECASE)
        if quote not in source_text or subject_match is None or observation_match is None:
            continue

        subject_text = subject_match.group(0)
        observation_text = observation_match.group(0)
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
            EvidenceObservation.value_text == observation_text,
            EvidenceObservation.subject_label == subject_text,
        ).first()
        if existing:
            continue

        hedge = _source_hedge_for_observation(quote, observation_text)
        time_reference = _source_time_for_observation(quote, observation_text)
        context_parts = [
            "Source-grounded extraction from a caregiver report; not independently verified as a clinical fact."
        ]
        if time_reference:
            context_parts.append(f"Caregiver time wording: {time_reference.group(0)}.")
        db.add(EvidenceObservation(
            person_id=person_id,
            evidence_id=evidence_id,
            observation_kind="caregiver_report",
            epistemic_status="uncertain" if hedge else "reported",
            subject_label=subject_text[:160],
            attribute="caregiver_reported_observation",
            value_text=observation_text[:2000],
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
