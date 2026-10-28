"""Deterministic uncertainty analysis for evidence-backed care workflows.

This module does not infer clinical facts or emit probability-like confidence scores.
It evaluates supplied claims and provenance metadata and returns explicit limitations.
"""
from __future__ import annotations

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
    items = [dict(item) for item in evidence if isinstance(item, dict)]
    conflicts = [dict(item) for item in (unresolved_conflicts or []) if isinstance(item, dict)]
    required = [field.strip() for field in (required_fields or []) if isinstance(field, str) and field.strip()]
    supplied_fields = {
        str(item.get("field")).strip().lower()
        for item in items if item.get("field") and item.get("value") not in (None, "")
    }
    missing = [field for field in required if field.lower() not in supplied_fields]
    classifications: List[str] = []
    limitations: List[str] = []
    explicit_uncertainty = any(pattern.search(claim) for pattern in _UNCERTAINTY_PATTERNS)
    if explicit_uncertainty:
        classifications.append("explicit_uncertainty")
    if not items:
        classifications.append("insufficient_evidence")
        limitations.append("No retrieved evidence records were supplied to this assessment.")
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
        if isinstance(observed_at, str):
            try:
                parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age_days = (now - parsed.astimezone(timezone.utc)).total_seconds() / 86400
                max_age = item.get("max_age_days")
                if isinstance(max_age, (int, float)) and max_age >= 0 and age_days > max_age:
                    stale.append(item)
            except ValueError:
                classifications.append("temporal_uncertainty")
                limitations.append("An evidence timestamp could not be parsed; its temporal validity is unknown.")
    if stale:
        classifications.append("outdated_information")
        limitations.append("Evidence exceeded its explicitly supplied freshness limit.")
    if source_access != "available":
        classifications.append("knowledge_boundary")
        limitations.append("A required source is not available; absence from this assessment is not a negative finding.")

    # Evidence completeness is descriptive, not a confidence estimate.
    completeness = "insufficient" if not items or missing else ("partial" if conflicts or unverified or stale else "provided")
    if conflicts:
        epistemic_state = "conflicting"
    elif "possible_reference_ambiguity" in classifications:
        epistemic_state = "ambiguous"
    elif not items or missing or source_access != "available":
        epistemic_state = "unknown"
    elif unverified or stale or explicit_uncertainty:
        epistemic_state = "partially_known"
    else:
        epistemic_state = "partially_known"  # Evidence presence alone never proves a claim.
    task_thresholds = {
        "summarize": "qualified_summary_permitted" if items else "insufficient",
        "identify_change": "possible_change_only" if items and not conflicts and not missing else "insufficient",
        "low_risk_next_step": "qualified_low_risk_step_permitted" if items else "insufficient",
        "high_consequence_assessment": "insufficient_without_independent_validation",
        "recommendation": "qualified_only" if items and not conflicts else "insufficient",
    }
    sufficiency = task_thresholds.get(task, "task_rule_not_defined")
    if conflicts or missing or source_access != "available":
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
        "inferred_uncertainty": bool(classifications) and not explicit_uncertainty,
        "evidence_completeness": completeness,
        "source_reliability": "not_quantified",
        "conclusion_confidence": None,
        "confidence_semantics": "No calibrated probability is produced. Evidence presence is not proof.",
        "calibration_status": "not_calibrated",
        "sufficiency": sufficiency,
        "missing_fields": missing,
        "unresolved_conflicts": conflicts,
        "assumptions": [{
            "statement": "The supplied evidence list is complete for this request.",
            "status": "unverified",
            "introduced_by": "assessment_pipeline",
            "validation_needed": "Confirm that all relevant accessible records were retrieved.",
        }] if source_access == "available" else [],
        "alternatives": [],
        "clarification_questions": questions,
        "evidence_refs": evidence_refs,
        "limitations": list(dict.fromkeys(limitations)),
        "review_required": bool(classifications or not items or conflicts or missing),
        "assessed_at": now.isoformat(),
        "method": "deterministic_evidence_metadata_rules_v1",
    }
