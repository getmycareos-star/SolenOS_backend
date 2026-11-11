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
        try:
            if pattern is _DATE_PATTERNS[0]:
                parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            else:
                year = int(match.group(3))
                if year < 100:
                    year += 2000 if year < 70 else 1900
                parsed = date(year, int(match.group(1)), int(match.group(2)))
            return parsed.isoformat()
        except ValueError:
            # Invalid dates remain missing and are explicitly flagged below.
            return None
    return None


def candidate_warnings(field_type: str, observation: str, excerpt: str) -> list[str]:
    warnings: list[str] = []
    if field_type == "medication":
        if not re.search(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml)\b", excerpt, re.I):
            warnings.append("Medication strength is not explicit in the quoted passage.")
        if not re.search(r"\b(take|dose|dosage|once|twice|daily|every|at bedtime|as needed|prn)\b", excerpt, re.I):
            warnings.append("Medication administration instructions are not explicit; dosage remains missing.")
        if re.search(r"\b(?:increase|decrease|stop|discontinue|hold|restart|replace)\b", excerpt, re.I):
            warnings.append("Medication change language requires caregiver or clinician reconciliation.")
    if re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", excerpt) and _date_from_excerpt(excerpt) is None:
        warnings.append("A date-like value could not be validated; event_date remains missing.")
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
    return {
        "candidate_id": str(uuid.uuid4()),
        "document_id": document_id,
        "page_number": page_number,
        "source_excerpt": excerpt,
        "field_type": field_type,
        "extracted_value": observation or None,
        "event_date": _date_from_excerpt(excerpt),
        "recorded_date": timestamp,
        "confidence": None,
        "review_status": "Pending",
        "warnings": warnings,
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


def apply_medication_conflict_warnings(candidates: list[dict[str, Any]]) -> None:
    """Flag differing instructions for the same extracted medication subject."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if candidate.get("field_type") != "medication":
            continue
        subject = str(candidate.get("subject") or "").strip().casefold()
        if subject:
            grouped.setdefault(subject, []).append(candidate)
    for rows in grouped.values():
        distinct = {str(row.get("observation") or "").strip().casefold() for row in rows}
        if len(distinct) > 1:
            for row in rows:
                warnings = row.setdefault("warnings", [])
                warning = "Conflicting medication instructions appear in this document; reconcile against the original and a clinician before updating the active medication record."
                if warning not in warnings:
                    warnings.append(warning)
