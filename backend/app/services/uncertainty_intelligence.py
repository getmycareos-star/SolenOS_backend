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
    re.compile(
        r"\b(i think|i believe|maybe|perhaps|possibly|not sure|unsure|uncertain|unclear|unknown|"
        r"not known|not documented|not recorded|not confirmed|unconfirmed|unverified|"
        r"cannot determine|unable to determine|insufficient information|not enough information|"
        r"incomplete information|might have|may have|could be|may be|might be|seems like|"
        r"appears to|apparently)\b",
        re.I,
    ),
)
_CAUSAL_PATTERNS = (
    re.compile(
        r"\b(caused by|caused|because of|due to|resulted in|led to|triggered by|"
        r"contributed to|precipitated by|brought on by|as a result of)\b",
        re.I,
    ),
)
# Flag referential ambiguity cues, not every ordinary use of "this" or "it"
# (for example, "this morning" is temporal context, not an ambiguous referent).
_PRONOUN_PATTERN = re.compile(
    r"^(it|they|them|he|she|that|this)\b|\b(it|they|them|he|she)\s+"
    r"(changed|happened|said|did|was|were|is|are|has|have|had)\b|\b(that|this)\s+one\b",
    re.I,
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def evidence_record_to_uncertainty_item(
    row: Any,
    *,
    role: str = "current",
    is_baseline: bool = False,
    source_lineage_id: Optional[str] = None,
    source_independence_status: Optional[str] = None,
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
        "source_id", "origin_id", "source_lineage_id", "source_independence_status",
        "max_age_days", "ambiguous", "interpretation_status",
    ):
        if key in metadata:
            item[key] = metadata[key]
    if source_lineage_id:
        item["source_lineage_id"] = source_lineage_id
    if source_independence_status:
        item["source_independence_status"] = source_independence_status
    return item


def analyze_uncertainty(
    *,
    claim: str,
    task: str,
    evidence: Sequence[Dict[str, Any]],
    required_fields: Optional[Sequence[str]] = None,
    unresolved_conflicts: Optional[Sequence[Dict[str, Any]]] = None,
    assumptions: Optional[Sequence[Dict[str, Any]]] = None,
    alternatives: Optional[Sequence[Dict[str, Any]]] = None,
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
    supplied_evidence_ids = {_text(item.get("id")) for item in raw_items if _text(item.get("id"))}

    def normalize_review_candidates(candidates, *, kind):
        normalized = []
        allowed_statuses = {"proposed", "unresolved", "accepted", "rejected", "superseded"}
        if candidates is None:
            return normalized
        if not isinstance(candidates, (list, tuple)):
            raise ValueError(f"{kind} must be a list of structured review candidates")
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                raise ValueError(f"{kind}[{index}] must be an object")
            description = _text(candidate.get("statement") or candidate.get("hypothesis") or candidate.get("description"))
            if not description:
                raise ValueError(f"{kind}[{index}] requires a non-empty statement or hypothesis")
            status = _text(candidate.get("status")).lower() or "unresolved"
            if status not in allowed_statuses:
                raise ValueError(f"{kind}[{index}] has unsupported status {status!r}")
            evidence_fields = {}
            invalid_refs = []
            for key in ("supporting_evidence_ids", "contradicting_evidence_ids", "evidence_ids"):
                refs = candidate.get(key, [])
                if not isinstance(refs, (list, tuple)) or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
                    raise ValueError(f"{kind}[{index}].{key} must be a list of non-empty evidence IDs")
                cleaned = list(dict.fromkeys(ref.strip() for ref in refs))
                evidence_fields[key] = cleaned
                invalid_refs.extend(ref for ref in cleaned if ref not in supplied_evidence_ids)
            supporting_ids = evidence_fields["supporting_evidence_ids"]
            verified_supporting_ids = [
                evidence_id for evidence_id in supporting_ids
                if evidence_id in supplied_evidence_ids
                and any(
                    _text(item.get("id")) == evidence_id and item.get("verified") is True
                    for item in raw_items
                )
            ]
            reviewer_id = _text(candidate.get("reviewed_by_caregiver_id"))
            rationale = _text(candidate.get("resolution_rationale"))
            # A caller-provided status alone is not an audited lifecycle event.
            # Terminal decisions need an authenticated reviewer (checked by the
            # API), rationale, valid references, and explicitly verified support.
            if invalid_refs:
                status = "unresolved"
            if status in {"accepted", "rejected", "superseded"} and (
                not reviewer_id or not rationale or invalid_refs
                or (
                    status == "accepted"
                    and (not supporting_ids or not verified_supporting_ids)
                )
            ):
                status = "unresolved"
            normalized.append({
                "statement" if kind == "assumptions" else "hypothesis": description,
                "status": status,
                **evidence_fields,
                "evidence_references_valid": not invalid_refs,
                "verified_supporting_evidence_ids": verified_supporting_ids,
                "has_verified_supporting_evidence": bool(verified_supporting_ids),
                "support_validation_scope": "reference_and_source_status_only_not_semantic_entailment",
                "engine_semantic_support_verified": False,
                "decision_attribution": "caregiver_review" if status in {"accepted", "rejected", "superseded"} else "unresolved_or_proposed",
                "invalid_evidence_ids": list(dict.fromkeys(invalid_refs)),
                "reviewed_by_caregiver_id": reviewer_id or None,
                "resolution_rationale": rationale or None,
            })
        return normalized

    normalized_assumptions = normalize_review_candidates(assumptions, kind="assumptions")
    normalized_alternatives = normalize_review_candidates(alternatives, kind="alternatives")
    items = raw_items
    duplicate_groups: list[dict[str, Any]] = []
    seen_evidence_ids: dict[str, int] = {}
    seen_source_text: dict[str, int] = {}
    seen_lineage: dict[str, int] = {}
    for index, item in enumerate(raw_items):
        evidence_id = _text(item.get("id"))
        source_text = " ".join(_text(item.get("text")).casefold().split())
        # Explicit independence metadata wins over text-only matching. Two
        # independent witnesses can use identical wording; conversely, two
        # derived records can share a lineage even when their excerpts differ.
        independence = _text(item.get("source_independence_status")).lower()
        lineage_key = next((
            _text(item.get(key)) for key in ("source_lineage_id", "origin_id", "source_id")
            if _text(item.get(key))
        ), "")
        duplicate_of = None
        duplicate_reason = None
        if evidence_id and evidence_id in seen_evidence_ids:
            duplicate_of = seen_evidence_ids[evidence_id]
            duplicate_reason = "same_evidence_id"
        elif lineage_key and lineage_key in seen_lineage:
            duplicate_of = seen_lineage[lineage_key]
            duplicate_reason = "shared_source_lineage"
        elif (
            source_text
            and source_text in seen_source_text
            and independence not in {"independent", "independent_source", "independently_observed"}
        ):
            duplicate_of = seen_source_text[source_text]
            duplicate_reason = "identical_normalized_source_text"
        if duplicate_of is not None:
            duplicate_groups.append({
                "record_index": index,
                "duplicate_of_index": duplicate_of,
                "reason": duplicate_reason,
                "independent_corroboration": False,
            })
        else:
            if evidence_id:
                seen_evidence_ids[evidence_id] = index
            if source_text and source_text not in seen_source_text:
                seen_source_text[source_text] = index
            if lineage_key:
                seen_lineage[lineage_key] = index
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
    unresolved_assumptions = [item for item in normalized_assumptions if item["status"] in {"proposed", "unresolved"}]
    unresolved_alternatives = [item for item in normalized_alternatives if item["status"] in {"proposed", "unresolved"}]
    invalid_candidate_refs = [
        item for item in (*normalized_assumptions, *normalized_alternatives)
        if not item["evidence_references_valid"]
    ]
    if unresolved_assumptions:
        classifications.append("unresolved_assumptions")
        limitations.append("One or more explicit assumptions remain unresolved and must not be treated as established facts.")
    if unresolved_alternatives:
        classifications.append("unresolved_alternative_hypotheses")
        limitations.append("One or more alternative hypotheses remain unresolved and must not be treated as established explanations.")
    if invalid_candidate_refs:
        classifications.append("invalid_candidate_evidence_reference")
        limitations.append("At least one assumption or alternative references evidence outside the records supplied to this assessment.")
    if normalized_assumptions or normalized_alternatives:
        classifications.append("candidate_support_not_semantically_verified")
        limitations.append("Candidate evidence links are checked for record presence and source status only; the engine does not semantically prove that a cited record entails an assumption or alternative.")
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
    invalid_temporal_items = []
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
                    invalid_temporal_items.append(item)
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
                invalid_temporal_items.append(item)
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
        or invalid_temporal_items or missing_source_metadata or explicit_uncertainty
        or evidence_uncertainty or duplicate_groups or normalized_access not in {"available", ""} else "provided"
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
    # Do not let a task-level "qualified" label survive a known blocker. These
    # checks are deliberately conservative: the engine can still return a
    # bounded summary, but must not label the evidence sufficient for downstream
    # decision-making when source integrity or temporal validity is unresolved.
    sufficiency_blockers = (
        conflicts
        or missing
        or normalized_access not in {"available", ""}
        or ambiguity_signals
        or quality_limited
        or unverified
        or stale
        or missing_time
        or invalid_temporal_items
        or missing_source_metadata
        or explicit_uncertainty
        or evidence_uncertainty
        or "source_verification_uncertainty" in classifications
        or "causal_inference_review" in classifications
        or unresolved_assumptions
        or unresolved_alternatives
        or invalid_candidate_refs
        or (task in {"identify_change", "change_detection", "compare_state"} and "missing_baseline" in classifications)
    )
    if sufficiency_blockers:
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
    if unverified:
        questions.append({
            "target_field": None,
            "question": "Can the relevant source be checked or verified before relying on this information?",
            "reason": "At least one evidence item is not explicitly verified.",
            "priority": "high" if task in {"high_consequence_assessment", "recommendation"} else "medium",
        })
    if normalized_access not in {"available", ""}:
        questions.append({
            "target_field": None,
            "question": "Can source access be restored or the missing source material be retrieved before drawing a conclusion?",
            "reason": unavailable_reasons.get(
                normalized_access,
                "The source has a nonstandard access status, so completeness cannot be assumed.",
            ),
            "priority": "high" if task in {"high_consequence_assessment", "recommendation"} else "medium",
        })
    if stale:
        questions.append({
            "target_field": None,
            "question": "Can the current status be confirmed against a source with an appropriate freshness window?",
            "reason": "At least one evidence item exceeded its supplied freshness limit.",
            "priority": "high" if task in {"high_consequence_assessment", "recommendation"} else "medium",
        })
    if missing_time or invalid_temporal_items:
        questions.append({
            "target_field": None,
            "question": "When was this information observed or when did it take effect?",
            "reason": "Temporal validity is missing, malformed, or inconsistent with the assessment clock.",
            "priority": "high" if task in {"high_consequence_assessment", "recommendation"} else "medium",
        })
    if quality_limited:
        questions.append({
            "target_field": None,
            "question": "Can the original source be reviewed or re-extracted so the relevant content is readable and complete?",
            "reason": "At least one source has poor quality or incomplete extraction.",
            "priority": "high" if task in {"high_consequence_assessment", "recommendation"} else "medium",
        })
    if any(pattern.search(claim) for pattern in _CAUSAL_PATTERNS):
        questions.append({
            "target_field": None,
            "question": "What evidence supports a causal relationship rather than a temporal association?",
            "reason": "Causal wording requires evidence beyond sequence or correlation.",
            "priority": "high" if task == "high_consequence_assessment" else "medium",
        })
    if unresolved_assumptions:
        questions.append({
            "target_field": None,
            "question": "Which assumptions can be verified, rejected, or kept explicitly unresolved?",
            "reason": "An assumption must not silently become an established fact.",
            "priority": "high" if task in {"high_consequence_assessment", "recommendation"} else "medium",
        })
    if unresolved_alternatives:
        questions.append({
            "target_field": None,
            "question": "What evidence would distinguish the unresolved alternative explanations?",
            "reason": "The engine cannot select an alternative without discriminating evidence.",
            "priority": "high" if task in {"high_consequence_assessment", "recommendation"} else "medium",
        })
    if invalid_candidate_refs:
        questions.append({
            "target_field": None,
            "question": "Can each assumption or alternative be linked to evidence retrieved for this care record?",
            "reason": "One or more candidate evidence references were not present in the supplied evidence set.",
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
        # A classification is not automatically evidence of uncertainty. For
        # example, duplicate provenance is a quality finding, not a statement
        # that the claim itself is uncertain. Keep inferred uncertainty limited
        # to classifications that directly describe epistemic limitations.
        "inferred_uncertainty": any(
            classification in {
                "insufficient_evidence",
                "knowledge_boundary",
                "low_quality_evidence",
                "source_attribution_incomplete",
                "temporal_uncertainty",
                "missing_baseline",
                "ambiguous_source_interpretation",
                "missing_information",
                "conflicting_evidence",
                "possible_reference_ambiguity",
                "causal_inference_review",
                "source_verification_uncertainty",
                "outdated_information",
                "unsupported_assessment_task",
            }
            for classification in classifications
        ) and not (explicit_uncertainty or evidence_uncertainty),
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
        "assumptions": normalized_assumptions,
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
        "alternatives": normalized_alternatives,
        "clarification_questions": questions,
        "evidence_refs": evidence_refs,
        "evidence_record_count": len(raw_items),
        "independent_evidence_record_count": len(independent_items),
        "duplicate_evidence_groups": duplicate_groups,
        "source_access_status": normalized_access or "unspecified",
        "limitations": list(dict.fromkeys(limitations)),
        # This engine intentionally never upgrades evidence presence to a
        # verified claim. Every result therefore remains reviewable; this flag
        # must not become false merely because metadata checks found no issue.
        "review_required": True,
        "assessed_at": now.isoformat(),
        "method": "deterministic_evidence_metadata_rules_v1",
    }
