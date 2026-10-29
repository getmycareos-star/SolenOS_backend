"""Deterministic uncertainty analysis for evidence-backed care workflows.

This module does not infer clinical facts or emit probability-like confidence scores.
It evaluates supplied claims and provenance metadata and returns explicit limitations.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence


_UNCERTAINTY_PATTERNS = (
    re.compile(r"\b(i think|i believe|maybe|perhaps|possibly|not sure|unsure|might have|may have|seems like|apparently)\b", re.I),
)
_CAUSAL_PATTERNS = (
    re.compile(r"\b(caused by|because of|due to|resulted in|led to)\b", re.I),
)
_PRONOUN_PATTERN = re.compile(r"\b(it|they|them|he|she|that|this)\b", re.I)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def evidence_record_to_uncertainty_item(
    row: Any, *, role: str = "current", is_baseline: bool = False
) -> Dict[str, Any]:
    """Convert a persisted evidence row without promoting optional metadata to verified truth."""
    metadata: Dict[str, Any] = {}
    raw_metadata = getattr(row, "extra_metadata", None)
    if isinstance(raw_metadata, str) and raw_metadata:
        try:
            parsed = json.loads(raw_metadata)
            if isinstance(parsed, dict):
                metadata = parsed
        except (TypeError, ValueError):
            metadata = {}
    text = getattr(row, "source_text", None) or ""
    item: Dict[str, Any] = {
        "id": getattr(row, "id", None),
        "source_type": getattr(row, "source_type", None) or getattr(row, "type", None),
        "text": text,
        "verified": str(getattr(row, "evidence_status", None) or "").lower() == "verified",
        # A provenance label is not an occurrence timestamp.
        "temporal_provenance": getattr(row, "time_provenance", None),
        "readable": metadata.get("readable") if isinstance(metadata.get("readable"), bool) else bool(text.strip()),
        "role": role,
        "is_baseline": is_baseline,
    }
    for key in (
        "quality", "extraction_status", "temporal_scope", "observed_at", "effective_at",
        "source_id", "origin_id", "source_lineage_id", "max_age_days", "ambiguous",
        "interpretation_status",
    ):
        if key in metadata:
            item[key] = metadata[key]
    return item


def analyze_uncertainty(
    *,
    claim: str,
    task: str,
    evidence: Sequence[Dict[str, Any]],
    required_fields: Optional[Sequence[str]] = None,
    unresolved_conflicts: Optional[Sequence[Dict[str, Any]]] = None,
    source_access: str = "available",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Assess uncertainty without inventing evidence or calibrated probabilities.

    Evidence items must refer to records actually retrieved by the caller. Each item
    may include id, text, source_type, verified, observed_at, and temporal_scope.
    This function does not treat caller-provided text as independently verified.
    """
    claim = _text(claim)
    task = _text(task).lower()
    if not claim:
        raise ValueError("claim must contain the actual statement being assessed")
    if not task:
        raise ValueError("task must identify the assessment purpose")

    now = now or datetime.now(timezone.utc)
    # Normalize caller-supplied clocks. Naive timestamps are interpreted as UTC,
    # never as the machine's local timezone, so CI and production agree.
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    # Retain the supplied records for provenance, but separately identify repeated
    # copies. Repetition is not independent corroboration and must never inflate
    # the assessment's epistemic status.
    raw_items = [dict(item) for item in evidence if isinstance(item, dict)]
    conflicts = [dict(item) for item in (unresolved_conflicts or []) if isinstance(item, dict)]
    required = [field.strip() for field in (required_fields or []) if isinstance(field, str) and field.strip()]
    items = raw_items
    duplicate_groups: list[dict[str, Any]] = []
    seen_evidence_ids: dict[str, int] = {}
    seen_source_text: dict[str, int] = {}
    for index, item in enumerate(raw_items):
        evidence_id = _text(item.get("id"))
        source_text = " ".join(_text(item.get("text")).casefold().split())
        duplicate_of = None
        if evidence_id and evidence_id in seen_evidence_ids:
            duplicate_of = seen_evidence_ids[evidence_id]
        elif source_text and source_text in seen_source_text:
            duplicate_of = seen_source_text[source_text]
        if duplicate_of is not None:
            duplicate_groups.append({
                "record_index": index,
                "duplicate_of_index": duplicate_of,
                "reason": "same_evidence_id" if evidence_id and evidence_id in seen_evidence_ids else "identical_normalized_source_text",
                "independent_corroboration": False,
            })
        else:
            if evidence_id:
                seen_evidence_ids[evidence_id] = index
            if source_text:
                seen_source_text[source_text] = index
    independent_items = [
        item for index, item in enumerate(raw_items)
        if not any(group["record_index"] == index for group in duplicate_groups)
    ]
    supplied_fields = {
        str(item.get("field")).strip().lower()
        for item in items if item.get("field") and item.get("value") not in (None, "")
    }
    missing = [field for field in required if field.lower() not in supplied_fields]
    classifications: List[str] = []
    limitations: List[str] = []
    explicit_uncertainty = any(pattern.search(claim) for pattern in _UNCERTAINTY_PATTERNS)
    evidence_uncertainty = any(
        pattern.search(_text(item.get("text")))
        for item in items
        for pattern in _UNCERTAINTY_PATTERNS
    )
    if explicit_uncertainty:
        classifications.append("explicit_uncertainty")
    if evidence_uncertainty:
        classifications.append("uncertainty_in_source_evidence")
    if not items:
        classifications.append("insufficient_evidence")
        limitations.append("No retrieved evidence records were supplied to this assessment.")
    if duplicate_groups:
        classifications.append("duplicate_evidence")
        limitations.append("Repeated evidence records were detected and are not counted as independent corroboration.")
    unavailable_reasons = {
        "unavailable": "A required source is not available; absence from this assessment is not a negative finding.",
        "inaccessible": "A required source is inaccessible; absence from this assessment is not a negative finding.",
        "permission_denied": "Source access was denied; no conclusion may be drawn from the inaccessible source.",
        "authentication_failed": "Source authentication failed; retrieval is unverified and the source cannot be treated as reviewed.",
        "not_configured": "The required source is not configured; its contents are unknown, not negative.",
        "not_attempted": "The required source was not queried; its contents are unknown, not negative.",
        "timeout": "Source retrieval timed out; the result is incomplete, not a negative finding.",
        "rate_limited": "Source retrieval was rate-limited; the result is incomplete, not a negative finding.",
        "error": "A source operation returned an error; the result is incomplete, not a negative finding.",
        "failed": "A required retrieval or processing operation failed; the result is incomplete, not a negative finding.",
        "partial": "Only part of the relevant source material was retrieved; conclusions are bounded to the retrieved subset.",
        "truncated": "Retrieved source material was truncated; conclusions cannot be treated as a complete review.",
        "stale_cache": "The available material may be stale because a current source could not be confirmed.",
    }
    normalized_access = _text(source_access).lower()
    if normalized_access in unavailable_reasons:
        classifications.append("knowledge_boundary")
        limitations.append(unavailable_reasons[normalized_access])
    quality_limited = [
        item for item in items
        if _text(item.get("quality")).lower() in {"poor", "low", "unreadable", "partial", "unreliable"}
        or item.get("readable") is False
        or _text(item.get("extraction_status")).lower() in {"failed", "partial", "unreadable"}
    ]
    if quality_limited:
        classifications.append("low_quality_evidence")
        limitations.append("At least one source has poor quality, incomplete extraction, or unreadable content.")
    missing_source_metadata = [
        item for item in items
        if not _text(item.get("source_type")) and not _text(item.get("source"))
    ]
    if missing_source_metadata:
        classifications.append("source_attribution_incomplete")
        limitations.append("At least one evidence item lacks source-type attribution.")
    missing_time = [
        item for item in items
        if _text(item.get("temporal_scope")).lower() in {"current", "historical", "time_sensitive"}
        and not _text(item.get("observed_at"))
        and not _text(item.get("effective_at"))
    ]
    if missing_time:
        classifications.append("temporal_uncertainty")
        limitations.append("At least one time-sensitive evidence item has no usable observation or effective timestamp.")
    has_baseline = any(
        item.get("is_baseline") is True or _text(item.get("role")).lower() == "baseline"
        for item in items
    )
    has_comparison_observation = any(
        item.get("is_baseline") is not True and _text(item.get("role")).lower() != "baseline"
        for item in items
    )
    if task in {"identify_change", "change_detection", "compare_state"} and (not has_baseline or not has_comparison_observation):
        classifications.append("missing_baseline")
        if not has_baseline:
            limitations.append("No explicit comparison baseline was supplied; a change cannot be established from this assessment alone.")
        if not has_comparison_observation:
            limitations.append("No separate current/comparison observation was supplied alongside the baseline.")
    ambiguity_signals = [
        item for item in items
        if item.get("ambiguous") is True
        or _text(item.get("interpretation_status")).lower() in {"ambiguous", "unresolved", "needs_clarification"}
    ]
    if ambiguity_signals:
        classifications.append("ambiguous_source_interpretation")
        limitations.append("At least one source has an explicitly unresolved interpretation.");
    if missing:
        classifications.append("missing_information")
        limitations.append("Required fields absent from the supplied evidence: " + ", ".join(missing))
    if conflicts:
        classifications.append("conflicting_evidence")
        limitations.append("Unresolved conflicting records were supplied; this assessment does not choose a winner.")
    if _PRONOUN_PATTERN.search(claim):
        classifications.append("possible_reference_ambiguity")
        limitations.append("Pronouns or demonstratives occur in the claim; referent ambiguity requires contextual review.")
    if any(pattern.search(claim) for pattern in _CAUSAL_PATTERNS):
        classifications.append("causal_inference_review")
        limitations.append("Causal wording was detected; temporal association alone does not establish causation.")

    unverified = [item for item in items if item.get("verified") is not True]
    if unverified:
        classifications.append("source_verification_uncertainty")
        limitations.append("At least one supplied evidence item is not explicitly verified.")
    stale = []
    for item in items:
        observed_at = item.get("observed_at")
        if isinstance(observed_at, str) and observed_at.strip():
            try:
                parsed = datetime.fromisoformat(observed_at.strip().replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                parsed = parsed.astimezone(timezone.utc)
                age_days = (now - parsed).total_seconds() / 86400
                if age_days < 0:
                    classifications.append("temporal_uncertainty")
                    limitations.append("An evidence timestamp is in the future relative to the assessment clock; temporal ordering needs verification.")
                max_age = item.get("max_age_days")
                if (
                    isinstance(max_age, (int, float))
                    and not isinstance(max_age, bool)
                    and max_age >= 0
                    and age_days > max_age
                ):
                    stale.append(item)
            except (ValueError, OverflowError):
                classifications.append("temporal_uncertainty")
                limitations.append("An evidence timestamp could not be parsed; its temporal validity is unknown.")
    if stale:
        classifications.append("outdated_information")
        limitations.append("Evidence exceeded its explicitly supplied freshness limit.")
    if normalized_access not in {"available", ""} and normalized_access not in unavailable_reasons:
        classifications.append("knowledge_boundary")
        limitations.append("A required source has a nonstandard access status; source completeness cannot be assumed.")

    # Evidence completeness is descriptive, not a confidence estimate. Duplicate
    # copies do not increase independent support, and a missing baseline blocks
    # a change claim even if other records exist.
    completeness = "insufficient" if not independent_items or missing else (
        "partial" if conflicts or unverified or stale or quality_limited or missing_time
        or duplicate_groups or normalized_access not in {"available", ""} else "provided"
    )
    if conflicts:
        epistemic_state = "conflicting"
    elif "possible_reference_ambiguity" in classifications or ambiguity_signals:
        epistemic_state = "ambiguous"
    elif (
        not independent_items or missing or normalized_access not in {"available", ""}
        or "unsupported_assessment_task" in classifications
        or (task in {"identify_change", "change_detection", "compare_state"} and "missing_baseline" in classifications)
    ):
        # A present observation does not establish the separate claim that it
        # represents a change when the comparison prerequisite is missing.
        epistemic_state = "unknown"
    elif unverified or stale or explicit_uncertainty or quality_limited or missing_time:
        epistemic_state = "partially_known"
    else:
        epistemic_state = "partially_known"  # Evidence presence alone never proves a claim.
    task_thresholds = {
        "summarize": "qualified_summary_permitted" if items else "insufficient",
        "identify_change": "possible_change_only" if items and not conflicts and not missing and has_baseline and has_comparison_observation else "insufficient",
        "change_detection": "possible_change_only" if items and not conflicts and not missing and has_baseline and has_comparison_observation else "insufficient",
        "compare_state": "possible_change_only" if items and not conflicts and not missing and has_baseline and has_comparison_observation else "insufficient",
        "low_risk_next_step": "qualified_low_risk_step_permitted" if items else "insufficient",
        "high_consequence_assessment": "insufficient_without_independent_validation",
        "recommendation": "qualified_only" if items and not conflicts else "insufficient",
    }
    sufficiency = task_thresholds.get(task, "task_rule_not_defined")
    if task not in task_thresholds:
        classifications.append("unsupported_assessment_task")
        limitations.append("No sufficiency rule is defined for this assessment task; no downstream conclusion should be treated as established.")
        sufficiency = "insufficient_pending_resolution"
        # Unsupported tasks cannot inherit a knowledge state from generic evidence
        # heuristics. Evidence may exist, but this task has no authorized rule to
        # interpret it, so its task-specific conclusion remains unknown.
        epistemic_state = "unknown"
    if (
        conflicts or missing or normalized_access not in {"available", ""}
        or ambiguity_signals or quality_limited
        or (task in {"identify_change", "change_detection", "compare_state"} and "missing_baseline" in classifications)
    ):
        sufficiency = "insufficient_pending_resolution"
    questions = []
    for field in missing:
        questions.append({
            "target_field": field,
            "question": f"What is currently known about {field}?",
            "reason": "This required field is absent from the supplied retrieved evidence.",
            "priority": "high" if task in {"high_consequence_assessment", "recommendation"} else "medium",
        })
    for conflict in conflicts:
        questions.append({
            "target_field": conflict.get("field"),
            "question": "Can the conflicting records be reconciled against the original source or an appropriate professional?",
            "reason": "The supplied records disagree and the assessment cannot safely select one.",
            "priority": "high",
        })

    evidence_refs = [
        {"id": item.get("id"), "source_type": item.get("source_type"),
         "verified": item.get("verified") is True, "observed_at": item.get("observed_at")}
        for item in items
    ]
    return {
        "claim": claim,
        "task": task,
        "epistemic_state": epistemic_state,
        "classifications": list(dict.fromkeys(classifications)),
        "explicit_uncertainty": explicit_uncertainty,
        "uncertainty_in_source_evidence": evidence_uncertainty,
        "inferred_uncertainty": bool(classifications) and not (explicit_uncertainty or evidence_uncertainty),
        "evidence_completeness": completeness,
        "source_reliability": "not_quantified",
        "conclusion_confidence": None,
        "confidence_semantics": "No calibrated probability is produced. Evidence presence is not proof.",
        "calibration_status": "not_calibrated",
        "sufficiency": sufficiency,
        "missing_fields": missing,
        "unresolved_conflicts": conflicts,
        # The caller supplies a bounded set of retrieved records. The assessment
        # cannot independently prove that every relevant source was searched.
        "assumptions": [],
        "retrieval_scope": {
            "basis": "only_records_supplied_to_this_assessment",
            "completeness_verified": False,
            "unknown_relevant_sources_may_exist": True,
            "source_access_status": normalized_access or "unspecified",
        },
        "comparison_basis": {
            "explicit_baseline_present": has_baseline,
            "separate_comparison_observation_present": has_comparison_observation,
            "change_established_by_this_assessment": False,
        },
        "alternatives": [],
        "clarification_questions": questions,
        "evidence_refs": evidence_refs,
        "evidence_record_count": len(raw_items),
        "independent_evidence_record_count": len(independent_items),
        "duplicate_evidence_groups": duplicate_groups,
        "source_access_status": normalized_access or "unspecified",
        "limitations": list(dict.fromkeys(limitations)),
        "review_required": bool(classifications or not items or conflicts or missing),
        "assessed_at": now.isoformat(),
        "method": "deterministic_evidence_metadata_rules_v1",
    }
