"""Canonical, source-grounded candidate metadata for document intelligence.

This module never upgrades model output into a care fact. It annotates exact
source quotations with page provenance and deterministic validation warnings.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from typing import Any

_PAGE_MARKER = re.compile(r"(?m)^\[Page\s+(\d+)(?:\s+OCR)?\]\s*$")
_DATE_PATTERNS = (
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b"),
)
_NATURAL_DATE_TOKEN = re.compile(
    r"\b(?:\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)(?:\s+\d{4})?|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,?\s+\d{4})?)\b",
    re.I,
)
_MEDICATION_TERMS = re.compile(
    r"\b(mg|mcg|micrograms?|milligrams?|tablet|capsule|dose|dosage|"
    r"medication|medicine|take|twice daily|once daily|every\s+\d+\s+hours?)\b",
    re.I,
)
_MEDICATION_NAME = re.compile(r"\b([A-Z][a-zA-Z-]{3,})\s+(\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml))\b", re.I)


def page_for_excerpt(source_text: str, excerpt: str) -> int | None:
    """Return a 1-based page only when the exact quote resolves unambiguously."""
    if not excerpt or excerpt not in source_text:
        return None
    matches: list[int] = []
    markers = list(_PAGE_MARKER.finditer(source_text))
    if not markers:
        return 1
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(source_text)
        if excerpt in source_text[marker.end():end]:
            matches.append(int(marker.group(1)))
    return matches[0] if len(matches) == 1 else None


def classify_field(subject: str, observation: str, excerpt: str) -> str:
    text = " ".join((subject, observation, excerpt)).casefold()
    if _MEDICATION_TERMS.search(text):
        return "medication"
    if re.search(r"\b(allerg(?:y|ies|ic)|reaction)\b", text):
        return "allergy"
    if re.search(r"\b(diagnos(?:is|ed)|condition|disease)\b", text):
        return "diagnosis"
    if re.search(r"\b(follow[- ]?up|appointment|review|clinic|visit)\b", text):
        return "appointment"
    if re.search(r"\b(discharge|admitted|admission|hospitalized|hospitalised)\b", text):
        return "care_event"
    return "observation"


def _date_from_excerpt(excerpt: str) -> str | None:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(excerpt)
        if not match:
            continue
        prefix = excerpt[max(0, match.start() - 50):match.start()]
        if re.search(r"\b(?:recorded|documented|entered|signed|authored|created)\s+on\s+$", prefix, re.I):
            continue
        try:
            if pattern is _DATE_PATTERNS[0]:
                parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            else:
                # Slash-formatted dates are locale-ambiguous (MM/DD vs DD/MM).
                # Preserve them as source text until a caregiver confirms the format.
                return None
            return parsed.isoformat()
        except ValueError:
            # Invalid dates remain missing and are explicitly flagged below.
            return None
    for match in _NATURAL_DATE_TOKEN.finditer(excerpt):
        prefix = excerpt[max(0, match.start() - 60):match.start()]
        if re.search(r"\\b(?:recorded|documented|entered|signed|authored|created)\\s+on\\s+$", prefix, re.I):
            continue
        natural = match.group(0)
        for date_format in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
            try:
                return datetime.strptime(natural, date_format).date().isoformat()
            except ValueError:
                continue
    return None


def _recorded_date_from_excerpt(excerpt: str) -> str | None:
    match = re.search(
        r"\b(?:recorded|documented|entered|signed|authored|created)\s+on\s+(\d{4}-\d{2}-\d{2})\b",
        excerpt,
        re.I,
    )
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None


def _date_tokens(excerpt: str) -> list[str]:
    tokens: list[str] = []
    recorded_prefix = re.compile(
        r"\b(?:recorded|documented|entered|signed|authored|created)\s+on\s+$",
        re.I,
    )
    numeric_pattern = re.compile(
        r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4})\b"
    )
    for match in numeric_pattern.finditer(excerpt):
        prefix = excerpt[max(0, match.start() - 60):match.start()]
        if not recorded_prefix.search(prefix):
            tokens.append(match.group(0))
    for match in _NATURAL_DATE_TOKEN.finditer(excerpt):
        prefix = excerpt[max(0, match.start() - 60):match.start()]
        if recorded_prefix.search(prefix):
            continue
        natural = match.group(0)
        normalized = natural
        for date_format in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
            try:
                normalized = datetime.strptime(natural, date_format).date().isoformat()
                break
            except ValueError:
                continue
        tokens.append(normalized)
    return tokens


def candidate_warnings(field_type: str, observation: str, excerpt: str) -> list[str]:
    warnings: list[str] = []
    if field_type == "medication":
        if not re.search(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml)\b", excerpt, re.I):
            warnings.append("Medication strength is not explicit in the quoted passage.")
        if not re.search(r"\b(take|dose|dosage|once|twice|daily|every|at bedtime|as needed|prn)\b", excerpt, re.I):
            warnings.append("Medication administration instructions are not explicit; dosage remains missing.")
        if re.search(r"\b(?:increase|decrease|stop|discontinue|hold|restart|replace)\b", excerpt, re.I):
            warnings.append("Medication change language requires caregiver or clinician reconciliation.")
    date_tokens = _date_tokens(excerpt)
    recorded_date = _recorded_date_from_excerpt(excerpt)
    if recorded_date and not date_tokens:
        warnings.append("A document recording date is present, but the event date is not explicit; event_date remains missing.")
    elif date_tokens and _date_from_excerpt(excerpt) is None:
        if recorded_date:
            warnings.append("A document recording date is present, but the event date is not explicit; event_date remains missing.")
        else:
            warnings.append("Date format is ambiguous or invalid; event_date remains missing until confirmed.")
    if len(set(date_tokens)) > 1:
        warnings.append("Multiple dates appear in this passage; event_date remains unresolved until the relevant event date is confirmed.")
    if re.search(r"\b(previous|previously|prior|old|superseded|discontinued|replaced|no longer|do not use)\b", excerpt, re.I):
        warnings.append("This may be an outdated or superseded instruction; reconcile it before treating it as current.")
    if not excerpt.strip():
        warnings.append("No readable source excerpt is available.")
    return warnings


def canonical_candidate(
    item: dict[str, Any],
    *,
    document_id: str,
    source_text: str,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    excerpt = str(item.get("source_quote") or "")
    subject = str(item.get("subject") or "")
    observation = str(item.get("observation") or "")
    field_type = classify_field(subject, observation, excerpt)
    page_number = page_for_excerpt(source_text, excerpt)
    warnings = candidate_warnings(field_type, observation, excerpt)
    if page_number is None:
        warnings.append("The exact excerpt could not be assigned to one unambiguous page.")
    timestamp = (recorded_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    medication_details = None
    if field_type == "medication":
        strength_match = re.search(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml)\b", excerpt, re.I)
        dosage_match = re.search(
            r"[^.!?]*(?:take|dose|dosage|once|twice|daily|every|at bedtime|as needed|prn)[^.!?]*",
            excerpt,
            re.I,
        )
        medication_details = {
            "strength": strength_match.group(0).strip() if strength_match else None,
            "dosage_instructions": dosage_match.group(0).strip() if dosage_match else None,
        }
    return {
        "candidate_id": str(uuid.uuid4()),
        "document_id": document_id,
        "page_number": page_number,
        "source_excerpt": excerpt,
        "field_type": field_type,
        "extracted_value": observation or None,
        "event_date": None if len(set(_date_tokens(excerpt))) > 1 else _date_from_excerpt(excerpt),
        "recorded_date": _recorded_date_from_excerpt(excerpt),
        "extracted_at": timestamp,
        "confidence": None,
        "review_status": "Pending",
        "warnings": warnings,
        "medication_details": medication_details,
    }



def split_document_pages(source_text: str) -> list[tuple[int, str]]:
    """Split page-labelled extraction into independent Qwen inputs."""
    markers = list(_PAGE_MARKER.finditer(source_text or ""))
    if not markers:
        return [(1, (source_text or "").strip())] if (source_text or "").strip() else []
    pages: list[tuple[int, str]] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(source_text)
        body = source_text[marker.end():end].strip()
        if body and "[No readable text detected on this page]" not in body:
            pages.append((int(marker.group(1)), body))
    return pages


def chunk_document_pages(
    page_inputs: list[tuple[int, str]],
    *,
    max_chars: int = 12_000,
) -> list[tuple[int, str]]:
    """Split long pages into bounded Qwen inputs without dropping source text."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    chunks: list[tuple[int, str]] = []
    for page_number, page_text in page_inputs:
        remaining = page_text or ""
        while remaining:
            if len(remaining) <= max_chars:
                chunks.append((page_number, remaining))
                break
            boundary = remaining.rfind("\n", 0, max_chars)
            if boundary < max_chars // 2:
                boundary = max_chars
            else:
                boundary += 1
            chunk = remaining[:boundary]
            if chunk:
                chunks.append((page_number, chunk))
            remaining = remaining[boundary:]
    return chunks


def unreadable_page_numbers(source_text: str) -> list[int]:
    """Return page labels whose OCR/text extraction produced no usable passage."""
    markers = list(_PAGE_MARKER.finditer(source_text or ""))
    if not markers:
        return []
    unreadable: list[int] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(source_text)
        body = source_text[marker.end():end].strip()
        if not body or "[No readable text detected on this page]" in body:
            unreadable.append(int(marker.group(1)))
    return unreadable


def apply_medication_conflict_warnings(candidates: list[dict[str, Any]]) -> None:
    """Flag differing instructions for the same extracted medication subject."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if candidate.get("field_type") != "medication":
            continue
        subject = str(candidate.get("subject") or "").strip().casefold()
        source_text = " ".join(
            str(candidate.get(key) or "")
            for key in ("source_quote", "observation")
        )
        medication_match = _MEDICATION_NAME.search(source_text)
        medication_key = medication_match.group(1).casefold() if medication_match else subject
        if medication_key:
            grouped.setdefault(medication_key, []).append(candidate)
    for rows in grouped.values():
        distinct = {str(row.get("observation") or "").strip().casefold() for row in rows}
        if len(distinct) > 1:
            for row in rows:
                warnings = row.setdefault("warnings", [])
                warning = "Conflicting medication instructions appear in this document; reconcile against the original and a clinician before updating the active medication record."
                if warning not in warnings:
                    warnings.append(warning)



def classify_document(filename: str, source_text: str) -> dict[str, str]:
    """Conservative document-type routing; never infer a diagnosis or care fact."""
    text = f"{filename}\n{(source_text or '')[:12000]}".casefold()
    if re.search(r"\b(discharge summary|discharge instructions|after visit summary)\b", text):
        kind = "discharge_summary"
    elif re.search(r"\b(medication list|medication reconciliation|prescription list)\b", text):
        kind = "medication_list"
    elif re.search(r"\b(laboratory|lab results|blood test|pathology)\b", text):
        kind = "laboratory_results"
    elif re.search(r"\b(radiology|imaging report|x-ray|ct scan|mri)\b", text):
        kind = "imaging_report"
    elif re.search(r"\b(appointment|clinic visit|follow-up)\b", text):
        kind = "appointment_record"
    else:
        kind = "general_care_document"
    return {"document_type": kind, "classification_method": "deterministic_source_keyword", "classification_confidence": "unscored"}
