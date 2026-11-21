"""Persist source-linked Daily Care extraction and safe, non-authoritative observations."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.capture_analysis import CaptureAnalysis
from app.models.care import CareEvent, Caregiver, Evidence
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
    r"\b(seem(?:s|ed)?|appear(?:s|ed)?|possibly|maybe|might be|may be|might|may have|could have|"
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
    r"^\s*((?:[Tt]he\s+)?(?:patient|caregiver|doctor|nurse|care staff)|"
    r"(?:[Mm]y\s+)?(?:mother|father)|[Mm]um|[Mm]om|[Dd]ad|"
    r"[Ss]he|[Hh]e|[Tt]hey|[Ww]e|[Ii]|"
    r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
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
    for match in re.finditer(r"[^.!?;\n]+(?:[.!?;]+|$)", source_text or ""):
        sentence = match.group(0).strip()
        clauses = [part.strip() for part in re.split(r"\s+and\s+", sentence, flags=re.IGNORECASE)]
        valid_clauses = [
            part for part in clauses
            if len(re.findall(r"[A-Za-z0-9']+", part)) >= 2 and _DAILY_CLAUSE_VERB.search(part)
        ]
        if len(valid_clauses) < 2:
            continue

        sentence_subject = _DAILY_CLAUSE_SUBJECT.match(valid_clauses[0])
        inherited_subject = sentence_subject.group(1).strip() if sentence_subject else None
        if not inherited_subject:
            continue

        # A whole-sentence model candidate is less useful than the distinct,
        # independently traceable clauses. Replace only that exact collapse.
        observations = [
            item for item in observations
            if not (
                str(item.get("observation") or "").strip().rstrip(".!?;").casefold()
                == sentence.rstrip(".!?;").casefold()
                and str(item.get("source_quote") or "").strip() == sentence
            )
        ]
        existing_values = {
            str(item.get("observation") or "").strip().casefold()
            for item in observations
            if isinstance(item.get("observation"), str)
            and isinstance(item.get("source_quote"), str)
            and (
                item["source_quote"].strip() in sentence
                or sentence in item["source_quote"].strip()
            )
        }
        for clause in valid_clauses:
            subject_match = _DAILY_CLAUSE_SUBJECT.match(clause)
            subject = subject_match.group(1).strip() if subject_match else inherited_subject
            observation = clause.strip()
            key = observation.casefold()
            if any(existing and (existing in key or key in existing) for existing in existing_values):
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


def normalize_source_grounded_care_detail(
    *,
    source_text: str,
    observation_text: str,
) -> dict[str, Any] | None:
    """Give an explicitly reported weight-loss phrase a cautious display label.

    The exact source wording remains in value_text/source_passage. The normalized
    label is only a caregiver-report descriptor; it is never a measurement,
    diagnosis, confirmed change, or trend.
    """
    text_to_check = " ".join(str(source_text or "").split())
    positive = re.search(
        r"\b(?:los(?:ing|t)|loosing)(?:\s+some)?\s+weight\b"
        r"|\bweight\s+(?:loss|is\s+down|has\s+decreased)\b",
        text_to_check,
        re.IGNORECASE,
    )
    if positive is None:
        return None
    negative = re.search(
        r"\b(?:not|never|no\s+longer)\s+(?:currently\s+)?"
        r"(?:losing|loosing|lost)(?:\s+some)?\s+weight\b"
        r"|\bno\s+weight\s+loss\b"
        r"|\bden(?:y|ies|ied)\s+(?:any\s+)?(?:weight\s+loss|losing\s+weight)\b"
        r"|\bweight\s+loss\b.{0,32}\b(?:not|never|absent|denied)\b",
        text_to_check,
        re.IGNORECASE,
    )
    if negative is not None:
        return None

    amount_recorded = bool(re.search(
        r"\b\d+(?:[.,]\d+)?\s*(?:kg|kgs|kilograms?|lb|lbs|pounds?|stone)\b",
        text_to_check,
        re.IGNORECASE,
    ))
    timeframe_recorded = bool(re.search(
        r"\b(?:in|over|within|during|for|since|past|last)\s+"
        r"(?:the\s+)?(?:\d+\s*)?(?:days?|weeks?|months?|years?|fortnights?)\b",
        text_to_check,
        re.IGNORECASE,
    ))
    previous_measurement_recorded = bool(re.search(
        r"\b(?:previous|prior|last)\s+(?:recorded\s+)?weight\b"
        r"|\b(?:previously|before)\s+weighed\b",
        text_to_check,
        re.IGNORECASE,
    ))

    context_parts = [
        "Caregiver report; not independently confirmed.",
        "No amount was stated in this report." if not amount_recorded else "An amount was included in the caregiver report.",
        "No timeframe was stated in this report." if not timeframe_recorded else "A timeframe was included in the caregiver report.",
        (
            "A previous weight measurement was mentioned in this report."
            if previous_measurement_recorded
            else "No previous weight measurement was found in this report."
        ),
        "Clinical interpretation has not been established.",
    ]
    missing = []
    if not amount_recorded:
        missing.append("amount")
    if not timeframe_recorded:
        missing.append("timeframe")
    if not previous_measurement_recorded:
        missing.append("previous weight measurement")
    hedge = _source_hedge_for_observation(source_text, observation_text)
    return {
        "attribute": "reported_weight_change",
        "normalized_value": "possible weight loss",
        "context": " ".join(context_parts),
        "ambiguity_note": (
            "Not established from the available report: " + ", ".join(missing) + "."
            if missing else None
        ),
        "epistemic_status": "uncertain" if hedge else "reported",
        "hedge_marker": hedge.group(0) if hedge else None,
        "negated": False,
    }


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
    # The worker session disables autoflush. A database lookup cannot see rows
    # added earlier in this same extraction batch, so also deduplicate exact
    # source-span identities in memory before staging each observation.
    seen_source_keys: set[tuple[int, int, str, str, str]] = set()
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
        source_key = (start, end, quote, observation_text, subject_text)
        if source_key in seen_source_keys:
            continue
        seen_source_keys.add(source_key)
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

        normalized_detail = normalize_source_grounded_care_detail(
            source_text=quote,
            observation_text=observation_text,
        )
        if normalized_detail is not None and db.query(EvidenceObservation.id).filter(
            EvidenceObservation.person_id == person_id,
            EvidenceObservation.evidence_id == evidence_id,
            EvidenceObservation.source_span_start == start,
            EvidenceObservation.source_span_end == end,
            EvidenceObservation.extraction_method == "structured_daily_capture",
            EvidenceObservation.attribute == normalized_detail["attribute"],
        ).first():
            # Structured Daily Care already wrote the normalized detail for
            # this exact source span; do not project the model's paraphrase again.
            continue
        hedge = _source_hedge_for_observation(quote, observation_text)
        time_reference = _source_time_for_observation(quote, observation_text)
        context_parts = [
            "Source-grounded extraction from a caregiver report; not independently verified as a clinical fact."
        ]
        if time_reference:
            context_parts.append(f"Caregiver time wording: {time_reference.group(0)}.")
        if normalized_detail is not None:
            context_parts.append(normalized_detail["context"])
        db.add(EvidenceObservation(
            person_id=person_id,
            evidence_id=evidence_id,
            observation_kind="caregiver_report",
            epistemic_status=normalized_detail["epistemic_status"] if normalized_detail else ("uncertain" if hedge else "reported"),
            subject_label=subject_text[:160],
            attribute=normalized_detail["attribute"] if normalized_detail else "caregiver_reported_observation",
            value_text=observation_text[:2000],
            normalized_value=normalized_detail["normalized_value"] if normalized_detail else None,
            context=" ".join(context_parts),
            ambiguity_note=(\n                normalized_detail["ambiguity_note"]\n                if normalized_detail and normalized_detail["ambiguity_note"]\n                else (\n                    "The caregiver wording is uncertain; this is not a diagnosis or a causal conclusion."\n                    if hedge else None\n                )\n            ),
            hedge_marker=normalized_detail["hedge_marker"] if normalized_detail else (hedge.group(0) if hedge else None),
            negated=normalized_detail["negated"] if normalized_detail else None,
            reported_at=reported_at,
            observer_label="Caregiver",
            observer_caregiver_id=caregiver_id,
            observation_method="natural-language caregiver report",
            source_passage=quote,
            source_span_start=start,
            source_span_end=end,
            extraction_method="qwen_source_grounded",
            extraction_version="daily-capture-source-span-v1",
            extraction_run_id=source_event_id,
            created_by_caregiver_id=caregiver_id,
        ))
        created += 1

    if created:
        db.flush()
    return created



def _capture_timestamp(value: Any):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def persist_structured_daily_capture_observations(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    evidence_id: str,
    source_event_id: str,
    source_text: str,
    detail: dict[str, Any] | None,
    reported_at,
    recorded_at=None,
) -> int:
    """Persist deterministic, source-span-bound details already supplied in Daily Care.

    This does not interpret free text or promote a caregiver report to a clinical
    fact. It records the note, ratings, measurements and medication details
    exactly as submitted, independently of optional model extraction.
    """
    if not isinstance(detail, dict) or not isinstance(source_text, str) or not source_text:
        return 0

    candidates: list[dict[str, Any]] = []

    note = detail.get("notes")
    if isinstance(note, str) and note.strip():
        quoted_note = json.dumps(note, ensure_ascii=False)
        marker = json.dumps("notes") + ":" + quoted_note
        marker_start = source_text.find(marker)
        if marker_start >= 0:
            passage_start = marker_start + len(json.dumps("notes") + ":")
            note_end = passage_start + len(quoted_note)
            candidates.append({
                "attribute": "caregiver_note",
                "value_text": note,
                "units": None,
                "context": "Original wording shared by a caregiver; not independently verified.",
                "source_passage": quoted_note,
                "source_span_start": passage_start,
                "source_span_end": note_end,
                "observed_at": None,
            })
            normalized_detail = normalize_source_grounded_care_detail(
                source_text=note,
                observation_text=note,
            )
            if normalized_detail is not None:
                candidates.append({
                    **normalized_detail,
                    "value_text": note,
                    "units": None,
                    "source_passage": quoted_note,
                    "source_span_start": passage_start,
                    "source_span_end": note_end,
                    "observed_at": None,
                    "observation_method": "Daily Care form; source-grounded care-detail normalization",
                    "extraction_version": "structured-daily-capture-v2",
                })

    def append_structured_items(key: str, attribute_for, value_for, units_for, context_for, time_for):
        items = detail.get(key)
        if not isinstance(items, list):
            return
        cursor = 0
        for raw in items[:12]:
            if not isinstance(raw, dict):
                continue
            fragment = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            fragment_start = source_text.find(fragment, cursor)
            if fragment_start < 0:
                # Do not invent a source span when the exact submitted fragment
                # cannot be found in the immutable evidence text.
                continue
            fragment_end = fragment_start + len(fragment)
            cursor = fragment_end
            attribute = attribute_for(raw)
            value_text = value_for(raw)
            if not isinstance(attribute, str) or not attribute.strip():
                continue
            if value_text is None or not str(value_text).strip():
                continue

            # Keep the complete structured source fragment. The caregiver
            # interface formats known fields into readable text while this
            # exact span preserves the original submitted structure.
            passage, start, end = fragment, fragment_start, fragment_end
            units = units_for(raw)
            candidates.append({
                "attribute": attribute[:160],
                "value_text": str(value_text)[:2000],
                "units": str(units)[:80] if units not in (None, "") else None,
                "context": context_for(raw),
                "source_passage": passage,
                "source_span_start": start,
                "source_span_end": end,
                "observed_at": _capture_timestamp(time_for(raw)),
            })

    def scale_attribute(raw):
        axis = raw.get("axis")
        return str(axis).strip().lower().replace(" ", "_") if isinstance(axis, str) and axis.strip() else None

    append_structured_items(
        "scales",
        scale_attribute,
        lambda raw: raw.get("score"),
        lambda raw: "score",
        lambda raw: str(raw.get("note")).strip() if isinstance(raw.get("note"), str) and raw.get("note").strip() else "Caregiver-entered rating; shown as reported.",
        lambda raw: raw.get("observed_at"),
    )

    vital_attributes = {
        "bp_systolic": "blood_pressure_systolic",
        "bp_diastolic": "blood_pressure_diastolic",
        "heart_rate": "heart_rate",
        "temperature": "temperature",
        "oxygen_saturation": "oxygen_saturation",
        "weight": "weight",
    }

    def vital_context(raw):
        # Preserve the caregiver's note, but never imply that an entered value
        # has passed clinical validation. This capture layer does not validate
        # or interpret measurements.
        note = raw.get("note")
        detail_note = str(note).strip() if isinstance(note, str) and note.strip() else None
        return "; ".join(part for part in (
            detail_note,
            "Caregiver-entered value preserved as reported; not validated as a clinically usable measurement.",
        ) if part)

    append_structured_items(
        "vitals",
        lambda raw: vital_attributes.get(str(raw.get("vital_type") or "").strip().lower(), str(raw.get("vital_type") or "").strip().lower().replace(" ", "_") or None),
        lambda raw: raw.get("value"),
        lambda raw: str(raw.get("unit")).strip() if isinstance(raw.get("unit"), str) and raw.get("unit").strip() else None,
        vital_context,
        lambda raw: raw.get("measured_at"),
    )

    append_structured_items(
        "medications",
        lambda raw: "medication",
        lambda raw: raw.get("medication_name") or raw.get("name"),
        lambda raw: None,
        lambda raw: "; ".join(
            part for part in (
                f"Dose recorded: {raw.get('dose')}" if raw.get("dose") not in (None, "") else "",
                f"Status: {raw.get('status')}" if raw.get("status") not in (None, "") else "",
                str(raw.get("note")).strip() if isinstance(raw.get("note"), str) and raw.get("note").strip() else "",
            ) if part
        ) or "Medication information entered by a caregiver.",
        lambda raw: raw.get("observed_at") or raw.get("recorded_at"),
    )

    for candidate in candidates:
        start = candidate["source_span_start"]
        end = candidate["source_span_end"]
        passage = candidate["source_passage"]
        if source_text[start:end] != passage:
            continue
        existing = db.query(EvidenceObservation.id).filter(
            EvidenceObservation.person_id == person_id,
            EvidenceObservation.evidence_id == evidence_id,
            EvidenceObservation.source_span_start == start,
            EvidenceObservation.source_span_end == end,
            EvidenceObservation.extraction_method == "structured_daily_capture",
            EvidenceObservation.attribute == candidate["attribute"],
        ).first()
        if existing:
            continue
        db.add(EvidenceObservation(
            person_id=person_id,
            evidence_id=evidence_id,
            observation_kind="caregiver_report",
            epistemic_status=candidate.get("epistemic_status") or "reported",
            subject_label="Care recipient",
            attribute=candidate["attribute"],
            value_text=candidate["value_text"],
            normalized_value=candidate.get("normalized_value"),
            units=candidate["units"],
            context=candidate["context"],
            ambiguity_note=candidate.get("ambiguity_note"),
            hedge_marker=candidate.get("hedge_marker"),
            negated=candidate.get("negated"),
            occurred_at=candidate["observed_at"],
            observed_at=candidate["observed_at"],
            reported_at=reported_at,
            recorded_at=recorded_at or reported_at,
            observer_label="Caregiver",
            observer_caregiver_id=caregiver_id,
            observation_method=candidate.get("observation_method") or "Daily Care form",
            source_passage=passage,
            source_span_start=start,
            source_span_end=end,
            extraction_method="structured_daily_capture",
            extraction_version=candidate.get("extraction_version") or "structured-daily-capture-v1",
            extraction_run_id=source_event_id,
            created_by_caregiver_id=caregiver_id,
        ))
    db.flush()
    return db.query(EvidenceObservation.id).filter(
        EvidenceObservation.person_id == person_id,
        EvidenceObservation.evidence_id == evidence_id,
        EvidenceObservation.extraction_method == "structured_daily_capture",
        EvidenceObservation.extraction_run_id == source_event_id,
    ).count()



def backfill_structured_daily_capture_observations(
    db: Session,
    *,
    person_id: str,
    limit: int = 200,
) -> dict[str, int | bool]:
    """Idempotently backfill source-linked details for older Daily Care entries.

    This is called only by the explicit, authenticated change-reassessment
    workflow. It never changes the original event or evidence source.
    """
    bounded_limit = max(1, min(int(limit), 500))
    events = (
        db.query(CareEvent)
        .filter(
            CareEvent.person_id == person_id,
            CareEvent.event_type.in_({
                "structured_daily_capture",
                "daily_care_entry",
                "daily_capture",
            }),
        )
        .order_by(CareEvent.recorded_at.desc(), CareEvent.id.asc())
        .limit(bounded_limit + 1)
        .all()
    )
    truncated = len(events) > bounded_limit
    events = events[:bounded_limit]
    evidence_ids = {
        str(evidence_id)
        for event in events
        for evidence_id in (event.evidence_ids or [])
        if evidence_id
    }
    evidence_rows = (
        db.query(Evidence)
        .filter(Evidence.person_id == person_id, Evidence.id.in_(evidence_ids))
        .all()
        if evidence_ids else []
    )
    evidence_by_id = {str(row.id): row for row in evidence_rows}
    candidate_caregiver_ids = {
        str(caregiver_id)
        for event in events
        for caregiver_id in [event.created_by_caregiver_id]
        if caregiver_id
    } | {
        str(row.uploaded_by_caregiver_id)
        for row in evidence_rows
        if row.uploaded_by_caregiver_id
    }
    valid_caregiver_ids = {
        row[0]
        for row in db.query(Caregiver.id).filter(
            Caregiver.person_id == person_id,
            Caregiver.id.in_(candidate_caregiver_ids),
        ).all()
    } if candidate_caregiver_ids else set()
    observations_available = 0
    evidence_missing = 0

    for event in events:
        try:
            parsed = json.loads(event.description) if isinstance(event.description, str) else event.description
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        detail = parsed.get("original_capture") if isinstance(parsed.get("original_capture"), dict) else parsed
        caregiver_id = event.created_by_caregiver_id
        for raw_evidence_id in (event.evidence_ids or []):
            evidence = evidence_by_id.get(str(raw_evidence_id))
            if evidence is None or not evidence.source_text:
                evidence_missing += 1
                continue
            source_caregiver_id = (
                caregiver_id if caregiver_id in valid_caregiver_ids
                else evidence.uploaded_by_caregiver_id
                if evidence.uploaded_by_caregiver_id in valid_caregiver_ids
                else None
            )
            if not source_caregiver_id:
                continue
            observations_available += persist_structured_daily_capture_observations(
                db,
                person_id=person_id,
                caregiver_id=source_caregiver_id,
                evidence_id=evidence.id,
                source_event_id=event.id,
                source_text=evidence.source_text,
                detail=detail,
                reported_at=getattr(event, "reported_at", None) or event.recorded_at,
                recorded_at=evidence.ingested_at or event.recorded_at,
            )

    return {
        "events_scanned": len(events),
        "observations_available": observations_available,
        "evidence_missing": evidence_missing,
        "truncated": truncated,
    }
