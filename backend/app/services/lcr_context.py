"""Permission-scoped, bounded retrieval shared by LCR-facing workflows.

This module is read-only. It projects existing canonical care-domain tables into a
single response contract; it does not promote assertions, create facts, or mutate
the Living Care Record.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Any
from uuid import UUID

from sqlalchemy import Enum as SAEnum
from sqlalchemy import MetaData, String, Table, and_, cast, desc, false, func, inspect, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.care_continuity import CareStateRevision
from app.services.epistemic_contract import assertion_information_status

# Only caregiver-relevant fields are exposed. Internal storage paths, raw tokens,
# and operational secrets are deliberately excluded.
SOURCE_SPECS: dict[str, dict[str, Any]] = {
    "living_record": {
        "table": "living_care_records",
        "required": True,
        "fields": ["id", "record_status", "schema_version", "created_at", "updated_at", "created_by_caregiver_id"],
        "order": ["updated_at", "created_at"],
    },
    "events": {
        "table": "care_events",
        "required": True,
        "fields": ["id", "event_type", "status", "occurred_at", "occurred_at_timezone", "recorded_at",
                   "title", "description", "location", "evidence_ids", "time_provenance",
                   "location_provenance", "created_by_caregiver_id", "significance_verdict",
                   "attention_candidate", "follow_up_candidate", "situation_id"],
        "order": ["occurred_at", "recorded_at", "created_at"],
    },
    "evidence": {
        "table": "evidence",
        "required": True,
        "fields": [
            "id", "type", "evidence_kind", "epistemic_status", "source_text", "uploaded_at",
            "uploaded_by_caregiver_id", "time_provenance", "location", "evidence_status",
            "source_type", "source_span_start", "source_span_end", "original_author",
            "reported_by_label", "reported_by_caregiver_id", "collected_by_caregiver_id",
            "collected_at", "occurred_at", "occurred_at_timezone", "origin_channel",
            "original_source_reference", "parent_evidence_id", "source_text_sha256",
            "ingested_at", "reported_at", "collection_method", "origin_device_ref",
            "transfer_source", "transfer_reference",
        ],
        "order": ["uploaded_at", "created_at"],
    },
    "evidence_reviews": {
        "table": "evidence_review_records",
        "required": False,
        "fields": [
            "id", "evidence_id", "review_type", "outcome", "rationale", "criteria",
            "policy_version", "source_attribution_snapshot", "reviewed_by_caregiver_id", "created_at",
        ],
        "order": ["created_at"],
    },
    "claim_assessments": {
        "table": "evidence_claim_assessments",
        "required": False,
        "fields": [
            "id", "claim_id", "claim_version", "status", "rationale", "policy_version",
            "evidence_links", "reviewed_by_caregiver_id", "supersedes_assessment_id",
            "superseded_by_claim_id", "created_at",
        ],
        "order": ["created_at"],
    },
    "evidence_freshness_assessments": {
        "table": "evidence_freshness_assessments",
        "required": False,
        "fields": [
            "id", "evidence_id", "policy_id", "policy_version", "freshness_status",
            "source_time", "source_time_kind", "assessed_at", "age_days", "rationale",
            "assessed_by_caregiver_id",
        ],
        "order": ["assessed_at"],
    },
    "evidence_freshness_policies": {
        "table": "evidence_freshness_policies",
        "required": False,
        "fields": [
            "id", "domain_key", "policy_version", "max_age_days", "historical_after_days",
            "rationale", "created_by_caregiver_id", "created_at",
        ],
        "order": ["created_at"],
    },
    "evidence_completeness_policies": {
        "table": "evidence_completeness_policies",
        "required": False,
        "fields": [
            "id", "domain_key", "purpose_key", "policy_version", "required_fields",
            "rationale", "created_by_caregiver_id", "created_at",
        ],
        "order": ["created_at"],
    },
    "evidence_completeness_assessments": {
        "table": "evidence_completeness_assessments",
        "required": False,
        "fields": [
            "id", "policy_id", "policy_version", "subject_type", "subject_id",
            "assessment_status", "required_fields", "satisfied_fields", "missing_fields",
            "field_evidence_json", "evidence_ids", "rationale", "assessment_basis",
            "semantic_sufficiency", "assessed_by_caregiver_id", "assessed_at",
        ],
        "order": ["assessed_at"],
    },
    "evidence_observations": {
        "table": "evidence_observations",
        "required": False,
        "fields": ["id", "evidence_id", "supersedes_observation_id", "correction_rationale",
                   "observation_kind", "epistemic_status", "subject_label", "attribute", "value_text",
                   "normalized_value", "units", "context", "occurred_at", "observed_at", "reported_at",
                   "observer_label", "observer_caregiver_id", "observation_method", "source_passage",
                   "source_span_start", "source_span_end", "negated", "hedge_marker", "ambiguity_note",
                   "extraction_method", "extraction_version", "extraction_run_id",
                   "created_by_caregiver_id", "recorded_at"],
        # Prefer recording order so a recent correction is not hidden behind
        # an older event-time value in a bounded source projection.
        "order": ["recorded_at", "created_at", "occurred_at"],
    },
    "facts": {
        "table": "care_facts",
        "required": True,
        "fields": ["id", "subject_type", "subject_id", "fact_type", "fact_value", "epistemic_status",
                   "effective_at", "recorded_at", "created_at", "superseded_by_fact_id",
                   "created_by_caregiver_id", "review_notes"],
        "order": ["recorded_at", "created_at", "effective_at"],
    },
    "document_extractions": {
        "table": "capture_analyses",
        "required": False,
        "fields": ["id", "source_evidence_id", "source_event_id", "source_text", "extraction_status", "created_at", "caregiver_id", "candidates"],
        "order": ["created_at"],
    },
    "assertions": {
        "table": "care_assertions",
        "required": True,
        "fields": ["id", "subject_type", "subject_id", "field", "assertion_kind", "asserted_value",
                   "source_event_id", "source_label", "source_text", "source_context", "source_conversation_id",
                   "source_message_id", "source_proposal_id", "event_time_text", "evidence_ids",
                   "epistemic_state", "assertion_status", "observed_at", "review_after_days", "created_by_caregiver_id",
                   "recorded_at"],
        "order": ["recorded_at", "created_at"],
    },
    "decisions": {
        "table": "care_decisions",
        "required": True,
        "fields": ["id", "title", "decision_text", "decision_status", "decided_at", "context",
                   "evidence_ids", "created_at", "created_by_caregiver_id"],
        "order": ["decided_at", "created_at"],
    },
    "decision_outcomes": {
        "table": "decision_outcomes",
        "required": False,
        "fields": ["id", "decision_id", "outcome_status", "observed_at", "description",
                   "evidence_ids", "is_intended_outcome", "created_at"],
        "order": ["observed_at", "created_at"],
    },
    "action_outcomes": {
        "table": "care_action_outcomes",
        "required": False,
        "fields": ["id", "action_id", "action_type", "outcome_status", "notes",
                   "correction_text", "evidence_ids", "created_by_caregiver_id", "created_at"],
        "order": ["created_at"],
    },
    "care_state_revisions": {
        "table": "care_state_revisions",
        "required": False,
        "fields": ["id", "target_type", "target_id", "revision_number", "change_kind",
                   "before_state", "after_state", "evidence_ids",
                   "changed_by_caregiver_id", "changed_at"],
        "order": ["changed_at"],
    },
    "change_analyses": {
        "table": "care_delta_runs",
        "required": False,
        "fields": ["id", "source", "event_date", "source_event_id", "evidence_ids",
                   "delta_json", "model_name", "created_by_caregiver_id", "created_at"],
        "order": ["created_at"],
    },
    "open_loop_events": {
        "table": "open_loop_events",
        "required": False,
        "fields": ["id", "loop_id", "caregiver_id", "event_type", "before_state",
                   "after_state", "evidence", "actor", "created_at"],
        "order": ["created_at"],
    },
    "open_loop_occurrences": {
        "table": "open_loop_occurrences",
        "required": False,
        "fields": ["id", "loop_id", "caregiver_id", "source_type", "source_id",
                   "source_fingerprint", "evidence", "occurred_at", "created_at"],
        "order": ["occurred_at", "created_at"],
    },
    "handoffs": {
        "table": "caregiver_handoffs",
        "required": False,
        "fields": [
            "id", "from_caregiver_id", "to_caregiver_id", "person_id",
            "responsibility_type", "scope", "timestamp", "effective_time",
            "context", "acceptance_status", "acceptance_evidence",
            "completion_evidence", "is_completed", "provenance", "created_at",
        ],
        "order": ["timestamp", "effective_time", "created_at"],
    },
    "timeline": {
        "table": "care_timeline_entries",
        "required": False,
        "fields": ["id", "source_type", "source_id", "entry_type", "title", "summary",
                   "occurred_at", "evidence_ids", "epistemic_status", "is_projection", "created_at"],
        "order": ["occurred_at", "created_at"],
    },
    "contradictions": {
        "table": "contradictions",
        "required": False,
        "fields": [
            "id", "person_id", "subject_type", "subject_id", "field", "description",
            "event_id", "context_key", "effective_from", "effective_to",
            "matching_candidate_ids", "evidence_ids", "source_assertion_ids",
            "resolution_status", "resolution_notes", "resolution_scope",
            "unresolved_remainder", "resolved_at", "resolved_by_caregiver_id",
            "created_at", "updated_at",
        ],
        "order": ["updated_at", "created_at"],
    },
    "uncertainties": {
        "table": "care_uncertainties",
        "required": False,
        "fields": ["id", "care_record_id", "target_type", "target_id", "question", "uncertainty_type",
                   "status", "source_ids", "created_by_caregiver_id", "created_at", "resolved_at"],
        "order": ["created_at"],
    },
    "uncertainty_assessments": {
        "table": "uncertainty_assessments",
        "required": False,
        "fields": ["id", "subject_type", "subject_id", "field", "epistemic_state", "confidence",
                   "known_value", "gap_reason", "context", "evidence_ids", "temporal_precision",
                   "assessed_at", "assessed_by_caregiver_id", "superseded_by_assessment_id", "is_active"],
        "order": ["assessed_at"],
    },
    "missing_information": {
        "table": "care_missing_information",
        "required": False,
        "fields": ["id", "care_record_id", "topic", "description", "priority", "status", "created_at", "resolved_at"],
        "order": ["created_at"],
    },
    "open_questions": {
        "table": "open_questions",
        "required": False,
        "fields": [
            "id", "question", "subject_type", "subject_id", "claim_id",
            "intended_answerer_role", "intended_answerer_label", "necessity",
            "necessity_rationale", "status", "priority", "evidence_ids", "context",
            "asked_at", "answered_at", "answer", "answer_provenance",
            "answered_by_caregiver_id", "closed_at", "closed_reason",
            "created_by_caregiver_id", "updated_at",
        ],
        "order": ["asked_at"],
    },
    "open_question_answer_events": {
        "table": "open_question_answer_events",
        "required": False,
        "fields": [
            "id", "question_id", "answer", "answer_provenance", "evidence_ids",
            "answer_version", "answered_by_caregiver_id", "created_at",
        ],
        "order": ["created_at"],
    },
    "open_question_review_events": {
        "table": "open_question_review_events",
        "required": False,
        "fields": [
            "id", "question_id", "answer_event_id", "answer_version",
            "decision", "rationale", "reviewer_caregiver_id",
            "claim_assessment_id", "resulting_claim_id", "created_at",
        ],
        "order": ["created_at"],
    },
    "open_loop_records": {
        "table": "open_loop_records",
        "required": False,
        "fields": [
            "id", "loop_type", "title", "description", "status", "outcome_status", "source_type", "source_id",
            "caregiver_id", "assigned_caregiver_id", "owner_role", "owner_label", "uncertainty", "missing_information", "options",
            "request_text", "recipient_label", "requested_at", "last_contact_at",
            "verification_rule", "deadline_at", "deadline_source", "deadline_timezone",
            "consequence_of_delay", "priority", "priority_reasons", "deferred_reason",
            "review_at", "inactive_since", "closure_result", "closed_at",
            "cancellation_reason", "recurrence_of_id", "created_at", "updated_at", "version",
        ],
        "order": ["updated_at", "created_at"],
    },
    "information_gaps": {
        "table": "information_gaps",
        "required": False,
        "fields": ["id", "subject_type", "subject_id", "field", "epistemic_state", "gap_reason", "priority", "lifecycle_status", "description", "expected_by", "resolution_date", "resolution_notes", "evidence_ids", "created_at", "updated_at", "created_by_caregiver_id"],
        "order": ["created_at"],
    },
    "follow_up_links": {
        "table": "care_follow_up_links",
        "required": False,
        "fields": ["id", "source_type", "source_id", "target_type", "target_id", "follow_up_kind", "status", "due_at", "completed_at", "notes", "evidence_ids", "owner_caregiver_id", "created_by_caregiver_id", "created_at"],
        "order": ["created_at"],
    },
    "reconciliations": {
        "table": "care_reconciliation_records",
        "required": False,
        "fields": ["id", "target_type", "target_id", "reconciliation_status", "competing_claim_ids", "evidence_ids", "decision_notes", "reviewed_by_caregiver_id", "reviewed_at", "created_at"],
        "order": ["created_at"],
    },
    "conflicts": {
        "table": "care_conflicts",
        "required": False,
        "fields": ["id", "care_record_id", "conflict_type", "status", "claim_refs", "description",
                   "resolution", "resolved_by_caregiver_id", "created_at", "resolved_at"],
        "order": ["created_at"],
    },
    "provenance": {
        "table": "record_provenance",
        "required": False,
        "fields": ["id", "care_record_id", "source_type", "source_id", "author_caregiver_id",
                   "author_label", "method", "recorded_at", "occurred_at", "effective_from",
                   "effective_to", "reason", "metadata", "created_at"],
        "order": ["recorded_at", "created_at"],
    },
    "caregiver_observations": {
        "table": "caregiver_observations",
        "required": False,
        "fields": ["id", "person_id", "observation_category", "description", "observed_value", "observed_at",
                   "recorded_at", "epistemic_status", "observation_type", "evidence_ids",
                   "source_type", "source_id", "reported_by_caregiver_id", "created_at"],
        "order": ["observed_at", "recorded_at", "created_at"],
    },
    "caregiver_contexts": {
        "table": "caregiver_contexts",
        "required": False,
        "fields": ["id", "context_type", "context_payload", "epistemic_status", "source_type",
                   "source_id", "recorded_at", "effective_from", "effective_to", "notes",
                   "reported_by_caregiver_id", "created_at"],
        "order": ["recorded_at", "created_at"],
    },
    "caregiver_perspectives": {
        "table": "caregiver_perspectives",
        "required": False,
        "fields": ["id", "topic", "perspective_text", "epistemic_status", "evidence_ids",
                   "source_type", "source_id", "recorded_at", "effective_from", "effective_to",
                   "supersedes_id", "reported_by_caregiver_id", "created_at"],
        "order": ["recorded_at", "created_at"],
    },
    "corrections": {
        "table": "corrections",
        "required": False,
        "fields": ["id", "target_type", "target_id", "original_text", "corrected_text", "reason",
                   "caregiver_id", "created_at", "processing_status", "processing_attempts",
                   "processing_error", "processed_at"],
        "order": ["created_at"],
    },
    "appointments": {
        "table": "appointments",
        "required": False,
        "fields": [
            "id", "title", "status", "scheduled_at", "scheduled_at_timezone",
            "location", "location_type", "provider", "healthcare_org",
            "preparation_notes", "documents_to_bring", "medication_questions",
            "travel_notes", "time_provenance", "location_provenance", "evidence_ids",
            "created_by_caregiver_id", "created_at", "updated_at", "completed_at", "resolved_at",
        ],
        "order": ["scheduled_at", "created_at"],
    },
    "appointment_workspaces": {
        "table": "appointment_workspace_records",
        "required": False,
        "fields": [
            "id", "appointment_id", "status", "version_number", "payload",
            "last_revision_reason", "created_by_caregiver_id", "updated_by_caregiver_id",
            "created_at", "updated_at",
        ],
        "order": ["updated_at", "created_at"],
    },
    "appointment_workspace_revisions": {
        "table": "appointment_workspace_revisions",
        "required": False,
        "fields": [
            "id", "record_id", "revision_number", "payload", "change_reason",
            "changed_by_caregiver_id", "changed_at",
        ],
        "order": ["changed_at"],
    },
}

SEARCH_FIELDS = {
    "living_record": ["record_status"],
    "events": ["event_type", "title", "description"],
    "evidence": ["type", "source_text", "source_type"],
    "evidence_observations": ["observation_kind", "epistemic_status", "subject_label", "attribute", "value_text", "context", "source_passage", "ambiguity_note"],
    "facts": ["subject_type", "fact_type", "fact_value", "review_notes"],
    "assertions": ["subject_type", "field", "asserted_value", "source_label", "source_text", "event_time_text", "assertion_status"],
    "document_extractions": ["source_text", "extraction_status"],
    "decisions": ["title", "decision_text", "context", "decision_status"],
    "decision_outcomes": ["outcome_status", "description"],
    "action_outcomes": ["action_type", "outcome_status", "notes", "correction_text"],
    "care_state_revisions": ["target_type", "change_kind", "before_state", "after_state"],
    "change_analyses": ["source", "delta_json", "model_name"],
    "open_loop_events": ["event_type", "actor"],
    "open_loop_occurrences": ["source_type", "source_id"],
    "handoffs": ["responsibility_type", "scope", "context", "acceptance_status", "acceptance_evidence", "completion_evidence", "provenance"],
    "timeline": ["entry_type", "title", "summary", "epistemic_status"],
    "uncertainties": ["question", "uncertainty_type", "status"],
    "uncertainty_assessments": ["subject_type", "subject_id", "field", "epistemic_state", "gap_reason", "known_value"],
    "missing_information": ["topic", "description", "priority", "status"],
    "information_gaps": ["subject_type", "field", "description", "epistemic_state", "gap_reason", "priority", "lifecycle_status"],
    "open_questions": ["question", "subject_type", "answer", "answer_provenance", "closed_reason", "intended_answerer_label", "necessity_rationale", "status"],
    "open_question_answer_events": ["answer", "answer_provenance"],
    "open_question_review_events": ["decision", "rationale"],
    "conflicts": ["conflict_type", "description", "resolution", "status"],
    "provenance": ["source_type", "author_label", "method", "reason"],
    "caregiver_observations": ["observation_category", "description", "source_type"],
    "caregiver_contexts": ["context_type", "notes", "epistemic_status"],
    "caregiver_perspectives": ["topic", "perspective_text", "epistemic_status"],
    "corrections": ["target_type", "original_text", "corrected_text", "reason"],
    "open_loop_records": ["loop_type", "title", "description", "status", "request_text", "recipient_label"],
    "appointments": [
        "title", "status", "location", "location_type", "provider", "healthcare_org",
        "preparation_notes", "travel_notes", "time_provenance", "location_provenance",
    ],
    "appointment_workspaces": ["status", "last_revision_reason", "payload"],
    "appointment_workspace_revisions": ["change_reason", "payload"],
}

def _text_search_predicates(table: Table, source_name: str, terms: list[str]):
    """Build safe lexical predicates for reflected text and enum columns.

    PostgreSQL ENUM values do not support ILIKE directly. Cast enum labels to
    VARCHAR before applying the same bounded, parameterized lexical search.
    Non-text, non-enum fields are deliberately excluded.
    """
    predicates = []
    for name in SEARCH_FIELDS.get(source_name, []):
        if name not in table.c:
            continue
        column = table.c[name]
        if isinstance(column.type, SAEnum):
            searchable_column = cast(column, String)
        elif isinstance(column.type, String):
            searchable_column = column
        elif source_name in {"appointment_workspaces", "appointment_workspace_revisions"} and name == "payload":
            searchable_column = cast(column, String)
        else:
            continue
        predicates.extend(
            searchable_column.ilike(f"%{term}%")
            for term in terms
        )
    return predicates


def _apply_text_search_filter(statement, table: Table, source_name: str, terms: list[str], *, include_all_history: bool = False) -> tuple[Any, bool]:
    """Apply lexical search and report whether the source can answer that search.

    Older or partially migrated source tables may not expose any configured
    searchable text columns. Returning every row from such a table for a
    topic-specific question creates unrelated care context and can mislead the
    Companion. Fail closed and disclose the source limitation; explicit
    full-history requests keep their separate, intentional behaviour.
    """
    if not terms or include_all_history:
        return statement, True
    predicates = _text_search_predicates(table, source_name, terms)
    if not predicates:
        return statement.where(false()), False
    return statement.where(or_(*predicates)), True


TEXT_FIELDS = {
    "source_text", "fact_value", "asserted_value", "source_label", "source_span", "description",
    "decision_text", "context", "summary", "title", "question", "answer", "answer_provenance",
    "notes", "correction_text", "before_state", "after_state", "delta_json",
    "closed_reason", "necessity_rationale", "intended_answerer_label", "rationale", "topic", "perspective_text",
    "preparation_notes", "travel_notes",
    "notes", "reason", "review_notes", "resolution",
}
MAX_TEXT_CHARS = 2400
JSON_TEXT_FIELDS = {
    "evidence_ids", "source_ids", "claim_refs", "competing_claim_ids",
    "before_state", "after_state", "delta_json",
    "source_assertion_ids", "matching_candidate_ids", "documents_to_bring", "medication_questions",
    "context", "metadata", "context_payload", "missing_information", "options", "priority_reasons",
}


def _safe_value(value: Any, key: str | None = None) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(v, key) for v in value]
    if isinstance(value, str) and key in JSON_TEXT_FIELDS and len(value) <= MAX_TEXT_CHARS and value.lstrip().startswith(("[", "{")):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (list, dict)):
                return _safe_value(parsed, key)
        except (TypeError, json.JSONDecodeError):
            pass
    if isinstance(value, str) and key in TEXT_FIELDS and len(value) > MAX_TEXT_CHARS:
        return value[:MAX_TEXT_CHARS] + "… [truncated]"
    return value


_SEMANTIC_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "uncertain": ("uncertainty", "unknown", "partial", "unreadable", "missing"),
    "unreadable": ("partial", "ocr", "not_readable"),
    "incomplete": ("partial", "unreadable", "not_processed"),
    "sleep": ("slept", "sleeping", "waking", "awake", "night", "rest"),
    "waking": ("awake", "sleep", "night"),
    "wandering": ("pacing", "walking", "roaming", "leaving"),
    # Preserve common mobility inflections so "walking" retrieves a source
    # sentence that says "walked" (and vice versa).
    "walking": ("walk", "walked"),
    "walked": ("walk", "walking"),
    "walk": ("walking", "walked"),
    "agitation": ("agitated", "distressed", "restless", "upset"),
    "medication": ("medicine", "dose", "tablet", "pill"),
    "medicine": ("medication", "dose", "tablet", "pill"),
    "fall": ("fell", "slipped", "injury"),
    "fell": ("fall", "slipped", "injury"),
    "eating": ("food", "meal", "appetite", "intake"),
    "drinking": ("fluid", "water", "hydration", "intake"),
    "pain": ("hurt", "sore", "discomfort"),
    "confusion": ("confused", "disoriented", "memory"),
    "confused": ("confusion", "disoriented", "memory"),
    "worse": ("worsening", "deteriorated", "declined"),
    "rest": ("sleep", "waking", "night"),
    "restless": ("sleep", "waking", "night", "agitation"),
}


_SEARCH_STOPWORDS = {
    "what", "when", "where", "which", "who", "why", "how", "did", "does", "was", "were",
    "the", "and", "for", "with", "from", "about", "that", "this", "have", "has", "had",
    "last", "past", "week", "month", "year", "today", "yesterday", "days", "day",
    "since", "before", "after", "between", "then", "there", "their", "them", "please",
    "her", "his", "our", "your", "been", "getting", "over", "into",
    "journey", "complete", "full", "whole", "timeline", "overview", "everything",
    "give", "tell", "show", "summarize", "summary", "history", "care",
    # Generic temporal-question verbs describe the requested operation, not a
    # care topic. Keeping them as lexical predicates makes questions such as
    # "What happened last week?" miss every record unless its text literally
    # contains "happened".
    "happened", "happen", "occurred", "occur", "event", "events",
    "changed", "change", "changes", "anything",
}


_TEMPORAL_RANGE_TOKENS = {
    "year", "years", "month", "months", "week", "weeks", "day", "days",
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
}


def _query_search_terms(
    query: str | None,
    *,
    temporal_window: dict[str, str] | None = None,
) -> list[str]:
    """Keep date-range words out of lexical matching once a time filter is resolved."""
    direct_terms = list(dict.fromkeys(
        term for term in re.findall(r"[A-Za-z0-9_-]{3,}", (query or "").casefold())
        if term not in _SEARCH_STOPWORDS
    ))[:8]
    if temporal_window:
        direct_terms = [
            term for term in direct_terms
            if term not in _TEMPORAL_RANGE_TOKENS
            and not re.fullmatch(r"20\d{2}(?:-\d{2}-\d{2})?", term)
        ]
    return direct_terms


def _expanded_search_terms(terms: list[str]) -> list[str]:
    terms = [term for term in terms if term not in _SEARCH_STOPWORDS]
    expanded = list(terms)
    for term in terms:
        expanded.extend(_SEMANTIC_EXPANSIONS.get(term, ()))
        if len(term) > 4 and term.endswith("ies"):
            expanded.append(term[:-3] + "y")
        elif len(term) > 4 and term.endswith("s"):
            expanded.append(term[:-1])
    return list(dict.fromkeys(expanded))[:24]


def _information_status(source_name: str, item: dict[str, Any]) -> tuple[str, str]:
    if source_name == "evidence_reviews":
        review_type = str(item.get("review_type") or "unknown").casefold()
        outcome = str(item.get("outcome") or "undetermined").casefold()
        return (
            f"evidence_review_{review_type}_{outcome}",
            "This is a caregiver-authored review of source quality or context. It does not independently verify a care fact or clinical instruction.",
        )
    if source_name == "claim_assessments":
        status = str(item.get("status") or "unassessed").casefold()
        return (
            f"claim_assessment_{status}",
            "This is the latest retrieved human claim-assessment status, not an independent clinical verification. Preserve its rationale, evidence links, and version.",
        )
    if source_name == "evidence_freshness_policies":
        return (
            "evidence_freshness_policy_defined",
            "This is an explicitly recorded caregiver-selected freshness policy. Its thresholds are policy boundaries, not clinical guidance or proof that a source is accurate.",
        )
    if source_name == "evidence_completeness_policies":
        return (
            "evidence_completeness_policy_defined",
            "This is a purpose-specific checklist for linked-source coverage. It does not establish semantic sufficiency or clinical completeness.",
        )
    if source_name == "evidence_freshness_assessments":
        status = str(item.get("freshness_status") or "undetermined").casefold()
        return (
            f"evidence_freshness_{status}",
            "Freshness is assessed only against the explicitly named policy and source-time basis; it does not establish factual accuracy.",
        )
    if source_name == "evidence_completeness_assessments":
        status = str(item.get("assessment_status") or "undetermined").casefold()
        sufficiency = str(item.get("semantic_sufficiency") or "not_automatically_verified").casefold()
        return (
            f"evidence_completeness_{status}",
            f"Completeness describes linked-field coverage only; semantic sufficiency is {sufficiency}. Do not treat linked-field coverage as proof that a care decision is adequately supported.",
        )
    if source_name == "care_state_revisions":
        return (
            "care_state_revision_history",
            "This is a saved change to a care-record entry. It shows how the record changed, not that the person's real-world condition changed. Preserve the before-and-after details, change time, and linked evidence.",
        )
    if source_name == "change_analyses":
        return (
            "recorded_change_analysis",
            "This is a previously saved analysis of care information, not an independently verified fact or diagnosis. Preserve its source event, supporting evidence, and model label; do not treat it as proof that a real-world change occurred.",
        )
    if source_name == "facts" and item.get("superseded_by_fact_id"):
        return (
            "superseded_fact",
            "This fact was replaced by a later record. Keep it in the history, but do not present it as current.",
        )
    if source_name == "assertions":
        return assertion_information_status(item.get("epistemic_state"), item.get("assertion_status"))
    if source_name == "appointment_workspaces":
        freshness = item.get("source_freshness") if isinstance(item.get("source_freshness"), dict) else {}
        freshness_status = str(freshness.get("status") or "unknown")
        if freshness_status == "stale":
            return (
                "appointment_workspace_stale",
                "This caregiver-authored appointment workspace was saved before a later care-record change. Keep it as historical context, but do not present it as the current appointment plan without review. Notes about what a clinician said are caregiver-recorded and not independently verified.",
            )
        if freshness_status != "current":
            return (
                "appointment_workspace_currentness_unknown",
                "This caregiver-authored appointment workspace is saved separately from the Living Care Record, and its currentness could not be confirmed. Treat it as historical context until reviewed.",
            )
        return (
            "appointment_workspace_caregiver_notes",
            "This is a caregiver-authored appointment workspace, saved separately from the Living Care Record. It may contain caregiver reports and notes about what a healthcare professional said; those statements are not independently verified. Preserve the saved status, version, and the payload's epistemic boundary.",
        )
    if source_name == "appointment_workspace_revisions":
        return (
            "appointment_workspace_revision_history",
            "This is a prior saved version of caregiver-authored appointment notes. It is historical context, not the current appointment plan or a verified care fact. Compare it with the latest workspace version before describing what is current.",
        )
    if source_name == "document_extractions":
        extraction_status = str(item.get("extraction_status") or "unknown").casefold()
        metadata = item.get("extraction_metadata") if isinstance(item.get("extraction_metadata"), dict) else {}
        unreadable = metadata.get("unreadable_page_numbers") or []
        unprocessed = metadata.get("unprocessed_page_numbers") or []
        # Older persisted rows may retain an "available" status even though
        # page-level metadata proves that one or more pages were never read.
        # The explicit page evidence takes precedence over the aggregate status.
        if extraction_status == "partial" or unreadable or unprocessed:
            page_gaps = []
            if unreadable:
                page_gaps.append("Unreadable pages: " + ", ".join(str(page) for page in unreadable) + ".")
            if unprocessed:
                page_gaps.append("Pages not yet processed: " + ", ".join(str(page) for page in unprocessed) + ".")
            detail = "Only part of this document could be read. Unreadable or unprocessed pages remain unknown; do not treat this extraction as the complete document."
            if page_gaps:
                detail += " " + " ".join(page_gaps)
            return ("document_extraction_partial", detail)
        if extraction_status == "failed":
            return (
                "document_extraction_failed",
                "The original document is saved, but text extraction failed. No care details can be established from this extraction.",
            )
        interpretation_status = str(metadata.get("interpretation_status") or "").casefold()
        if interpretation_status == "partial":
            return (
                "document_interpretation_partial",
                "The document text is available, but automated review did not finish across the full document. Treat any suggested details as incomplete and unreviewed.",
            )
        if interpretation_status == "failed":
            return (
                "document_interpretation_failed",
                "The document text is available, but automated interpretation failed. The original text remains source material and no AI interpretation should be treated as established.",
            )
        return (
            "document_extracted_unreviewed",
            "Extracted document text is source material, not a verified care fact or accepted LCR assertion.",
        )
    epistemic = item.get("epistemic_status") or item.get("epistemic_state")
    if epistemic:
        status = str(epistemic).casefold()
        if status in {"unknown", "uncertain", "unresolved", "needs_clarification", "missing"}:
            return status, "This record explicitly carries uncertainty; do not promote it to an established fact."
        return f"epistemic_{status}", "Preserve the recorded epistemic status; it is not upgraded by retrieval."
    for key in ("evidence_status", "record_status", "decision_status", "status"):
        if item.get(key) is not None:
            return f"recorded_{str(item[key]).casefold()}", "This is the stored workflow status, not an independent truth judgment."
    return "recorded_status_unspecified", "No explicit epistemic status was stored for this record."


def _safe_appointment_workspace_payload(value: Any) -> dict[str, Any]:
    """Keep caregiver-authored appointment context bounded and preserve its labels."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
    if not isinstance(value, dict):
        return {}
    text_fields = (
        "visitPurpose", "clinician", "specialty", "visitDate", "location", "attendees",
        "changes", "symptoms", "medicationQuestions", "historyQuestions", "practicalNotes",
        "concernsDiscussed", "caregiverReport", "clinicianFindings", "decisions",
        "instructions", "unanswered", "medicationReconciliation", "pendingResults",
        "familySummary", "reviewStatus",
    )
    result: dict[str, Any] = {}
    for key in text_fields:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            result[key] = item[:1200]
    for key in ("topConcerns", "documentsToBring", "attachedEvidenceIds", "attached_evidence_ids"):
        items = value.get(key)
        if isinstance(items, list):
            result[key] = [str(item)[:500] for item in items[:25] if isinstance(item, (str, int, float))]
    follow_ups = value.get("followUps", value.get("follow_ups"))
    if isinstance(follow_ups, list):
        result["followUps"] = [
            {
                field: str(item[field])[:500]
                for field in ("action", "owner", "due", "status", "open_loop_id", "reported_done_at")
                if item.get(field) is not None and isinstance(item.get(field), (str, int, float))
            }
            for item in follow_ups[:25]
            if isinstance(item, dict)
        ]
    boundary = value.get("epistemic_boundary")
    if isinstance(boundary, dict):
        result["epistemic_boundary"] = {
            key: _safe_value(boundary[key], key)
            for key in (
                "caregiver_report", "clinician_findings",
                "ai_suggestions_written_as_clinical_facts", "lcr_reconciliation",
            )
            if key in boundary
        }
    return result


def _document_extraction_metadata(value: Any, source_text: str | None = None) -> dict[str, Any]:
    """Expose bounded processing facts and fail closed on incomplete PDF page coverage."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
    if not isinstance(value, dict):
        return {}
    allowed = (
        "page_count",
        "processed_page_numbers",
        "unreadable_page_numbers",
        "unprocessed_page_numbers",
        "retryable_page_numbers",
        "retry_exhausted_page_numbers",
        "extraction_method",
    )
    metadata = {
        key: _safe_value(value.get(key), key)
        for key in allowed
        if key in value
    }

    mime_type = str(value.get("mime_type") or "").split(";", 1)[0].strip().casefold()
    filename = str(value.get("filename") or "").casefold()
    is_paginated_pdf = mime_type == "application/pdf" or filename.endswith(".pdf")
    page_count = value.get("page_count")
    if is_paginated_pdf and isinstance(page_count, int) and not isinstance(page_count, bool) and page_count > 0:
        processed = value.get("processed_page_numbers")
        manifest_pages = {
            page for page in processed
            if isinstance(page, int) and not isinstance(page, bool) and page > 0
        } if isinstance(processed, list) else set()
        if source_text is None:
            # Legacy rows may have a page manifest but predate stored page
            # markers in source_text. Preserve that explicit manifest rather
            # than falsely marking every page as missing.
            observed_pages = set(manifest_pages)
        else:
            observed_pages = {
                int(match.group(1))
                for match in re.finditer(r"(?m)^\[Page\s+(\d+)(?:\s+OCR)?\]", source_text)
            }
            # When immutable text exists, require both the text and manifest
            # to account for a page; a stale manifest cannot fill a source gap.
            if isinstance(processed, list):
                observed_pages.intersection_update(manifest_pages)
        explicit_unprocessed = value.get("unprocessed_page_numbers")
        unprocessed = {
            page for page in explicit_unprocessed
            if isinstance(explicit_unprocessed, list)
            and isinstance(page, int)
            and not isinstance(page, bool)
            and page > 0
        } if isinstance(explicit_unprocessed, list) else set()
        unprocessed.update(set(range(1, page_count + 1)) - observed_pages)
        if unprocessed:
            metadata["unprocessed_page_numbers"] = sorted(unprocessed)

    intelligence = value.get("document_intelligence")
    if isinstance(intelligence, dict):
        metadata["interpretation_status"] = intelligence.get("status")
        observations = intelligence.get("observations")
        metadata["unreviewed_candidate_count"] = len(observations) if isinstance(observations, list) else 0
    return metadata


def _retrieval_score(item: dict[str, Any], query: str, direct_terms: list[str], expanded_terms: list[str]) -> int:
    haystack = " ".join(
        str(value) for key, value in item.items()
        if key not in {"id", "evidence_ids", "candidates"} and value is not None
    ).casefold()
    record_terms = set(re.findall(r"[a-z0-9_-]{3,}", haystack))
    direct_hits = sum(1 for term in direct_terms if term in record_terms)
    expanded_hits = sum(1 for term in expanded_terms if term not in direct_terms and term in record_terms)
    phrase = 8 if query and query.casefold().strip() in haystack else 0
    return phrase + direct_hits * 4 + expanded_hits


@lru_cache(maxsize=16)
def _load_source_schema(bind: Any) -> tuple[frozenset[str], MetaData]:
    """Reflect the care-source schema once per SQLAlchemy engine.

    The API process is restarted after deployment migrations, so the cache is
    naturally refreshed on schema rollout. Keeping inspection out of each request
    avoids repeating database catalog queries and table reflection on hot paths.
    The returned metadata is treated as immutable by callers.
    """
    available_tables = frozenset(inspect(bind).get_table_names())
    metadata = MetaData()
    present = [
        spec["table"]
        for spec in SOURCE_SPECS.values()
        if spec["table"] in available_tables
    ]
    # Source lineage is auxiliary metadata for all LCR-facing workflows. Keep it
    # outside SOURCE_SPECS so its absence does not falsely mark core schema coverage
    # as incomplete on legacy databases.
    if "evidence_source_relationships" in available_tables:
        present.append("evidence_source_relationships")
    # Fact-to-evidence links are a separate canonical relation, not a column on
    # care_facts. Reflect it as auxiliary lineage metadata and enrich only the
    # facts already selected for this person-scoped projection.
    if "care_fact_evidence" in available_tables:
        present.append("care_fact_evidence")
    if present:
        metadata.reflect(bind=bind, only=list(dict.fromkeys(present)), resolve_fks=False)
    return available_tables, metadata


def build_lcr_context(
    db: Session,
    person_id: str,
    *,
    caregiver_id: str | None = None,
    limit_per_source: int = 20,
    query: str | None = None,
    temporal_window: dict[str, str] | None = None,
    include_all_history: bool = False,
    source_offsets: dict[str, int] | None = None,
    only_sources: set[str] | None = None,
) -> dict[str, Any]:
    """Return a person-scoped LCR projection with explicit retrieval coverage.

    Query and care-journey reads scan every matching row in each available source,
    then return a ranked per-source subset. Ordinary current-care reads remain
    bounded to recent records. Unavailable sources are explicitly marked.
    """
    limit = max(1, min(int(limit_per_source), 200))
    # Date-range words belong to the temporal filter, not lexical matching.
    # Written numbers such as "two" must not become care-text requirements.
    direct_terms = _query_search_terms(query, temporal_window=temporal_window)
    terms = _expanded_search_terms(direct_terms)
    bind = db.get_bind()
    available_tables, metadata = _load_source_schema(bind)

    sources: dict[str, dict[str, Any]] = {}
    missing_required: list[str] = []
    missing_optional: list[str] = []

    source_offsets = source_offsets or {}
    for source_name, spec in SOURCE_SPECS.items():
        if only_sources is not None and source_name not in only_sources:
            continue
        source_offset = max(0, int(source_offsets.get(source_name, 0) or 0))
        table_name = spec["table"]
        if table_name not in available_tables:
            (missing_required if spec["required"] else missing_optional).append(source_name)
            sources[source_name] = {
                "status": "unavailable_schema",
                "table": table_name,
                "items": [],
                "returned_count": 0,
                "has_more": False,
                "limit": limit,
            }
            continue

        table: Table = metadata.tables[table_name]
        if "person_id" not in table.c:
            (missing_required if spec["required"] else missing_optional).append(source_name)
            sources[source_name] = {
                "status": "unavailable_scope",
                "table": table_name,
                "items": [],
                "returned_count": 0,
                "has_more": False,
                "limit": limit,
            }
            continue

        field_names = [name for name in spec["fields"] if name in table.c]
        if "id" in table.c and "id" not in field_names:
            field_names.insert(0, "id")
        selected_columns = [table.c[name] for name in field_names]
        statement = select(*selected_columns).where(table.c.person_id == str(person_id))
        if source_name == "uncertainty_assessments" and "is_active" in table.c:
            statement = statement.where(table.c.is_active.is_(True))
        if source_name == "change_analyses" and "source" in table.c:
            # Situation-understanding snapshots are derived assessments, not
            # source change history. Including the latest snapshot in its own
            # next fingerprint makes every read look stale and every retry
            # create another assessment.
            statement = statement.where(table.c.source != "situation_understanding_assessment")
        if source_name == "care_state_revisions" and "target_type" in table.c:
            # Identity and caregiver-profile revisions are audit metadata, not
            # care-history entries for Companion retrieval.
            statement = statement.where(
                table.c.target_type.notin_(
                    ("persons", "caregivers", "caregiver_profiles", "caregiver_roles", "caregiver_responsibilities")
                )
            )
        if source_name == "assertions" and "assertion_status" in table.c:
            # Keep caregiver-attributed historical assertions so corrections and
            # rejections remain explainable. Model-only proposals without
            # caregiver attribution remain excluded.
            accepted_or_legacy = table.c.assertion_status.in_(("accepted", "caregiver_reviewed"))
            caregiver_origin = (
                table.c.created_by_caregiver_id.is_not(None)
                if "created_by_caregiver_id" in table.c else None
            )
            caregiver_report = and_(
                table.c.assertion_status == "reported",
                caregiver_origin,
            ) if caregiver_origin is not None else None
            historical_assertion = and_(
                table.c.assertion_status.in_(("rejected", "superseded", "withdrawn")),
                caregiver_origin,
            ) if caregiver_origin is not None else None
            eligible_assertions = [accepted_or_legacy]
            if caregiver_report is not None:
                eligible_assertions.append(caregiver_report)
            if historical_assertion is not None:
                eligible_assertions.append(historical_assertion)
            statement = statement.where(or_(*eligible_assertions))
        if source_name == "document_extractions":
            if caregiver_id and "caregiver_id" in table.c:
                statement = statement.where(table.c.caregiver_id == str(caregiver_id))
            if "extraction_status" in table.c:
                statement = statement.where(table.c.extraction_status.in_(("available", "partial")))
            if "candidates" in table.c:
                statement = statement.where(
                    table.c.candidates["kind"].as_string() == "document_extraction_v1"
                )
        if temporal_window:
            temporal_fields = {
                "events": ("occurred_at", "recorded_at"),
                "evidence": ("occurred_at", "uploaded_at"),
                "evidence_observations": ("occurred_at", "observed_at", "recorded_at"),
                "facts": ("effective_at", "recorded_at"),
                "assertions": ("observed_at", "recorded_at"),
                "document_extractions": ("created_at",),
                "evidence_reviews": ("created_at",),
                "claim_assessments": ("created_at",),
                "evidence_freshness_assessments": ("assessed_at",),
                "evidence_freshness_policies": ("created_at",),
                "evidence_completeness_assessments": ("assessed_at",),
                "evidence_completeness_policies": ("created_at",),
                "decisions": ("decided_at", "created_at"),
                "decision_outcomes": ("observed_at", "created_at"),
                "care_state_revisions": ("changed_at",),
                "change_analyses": ("event_date", "created_at"),
                "timeline": ("occurred_at", "created_at"),
                "caregiver_observations": ("observed_at", "recorded_at"),
                "caregiver_contexts": ("recorded_at",),
                "caregiver_perspectives": ("recorded_at",),
                "appointments": ("scheduled_at", "created_at"),
                "living_record": ("updated_at", "created_at"),
                "open_questions": ("asked_at", "created_at"),
                "open_loop_records": ("updated_at", "created_at"),
                "uncertainties": ("created_at",),
                "missing_information": ("created_at",),
            }.get(source_name, ("occurred_at", "observed_at", "effective_at", "scheduled_at", "recorded_at", "uploaded_at", "created_at", "updated_at"))
            temporal_columns = [table.c[name] for name in temporal_fields if name in table.c]
            if temporal_columns:
                start_value = datetime.fromisoformat(temporal_window["start"])
                end_value = datetime.fromisoformat(temporal_window["end"])
                # A delayed entry has two valid clocks: when the care event
                # happened and when it entered SolenOS. Coalescing these fields
                # silently hides a record when its event time is outside the
                # requested window but its recording time is inside it (or vice
                # versa). Include the row when either available timestamp falls
                # in the window; retain both timestamps in the returned record
                # so the caregiver can see the distinction.
                statement = statement.where(or_(*[
                    and_(column >= start_value, column <= end_value)
                    for column in temporal_columns
                ]))
            else:
                # Undated sources cannot satisfy an explicit temporal query.
                statement = statement.where(false())
        statement, source_searchable = _apply_text_search_filter(
            statement, table, source_name, terms,
            include_all_history=include_all_history,
        )
        if terms and not include_all_history and not source_searchable:
            sources[source_name] = {
                "status": "unsearchable",
                "table": table_name,
                "items": [],
                "returned_count": 0,
                "candidate_count": None,
                "candidate_scan_complete": False,
                "returned_history_complete": False,
                "has_more": False,
                "limit": limit,
                "offset": source_offset,
                "selection": "source_has_no_searchable_fields",
                "interpretation_limit": (
                    "This source is present, but its available fields cannot be searched for this topic. "
                    "No conclusion can be drawn that the source contains no matching care information."
                ),
            }
            continue
        order_field = next((name for name in spec["order"] if name in table.c), None)
        if order_field:
            statement = statement.order_by(desc(table.c[order_field]))
        if "id" in table.c and order_field != "id":
            statement = statement.order_by(desc(table.c.id))
        # Query-driven retrieval scans every matching row in deterministic
        # database batches, then retains only the best per-source results.
        # This removes the former 250-row candidate ceiling that could hide an
        # older, more relevant event behind newer lexical matches. Non-query
        # current-care reads intentionally remain bounded recent projections.
        # With no search or temporal filter, every candidate has the same
        # retrieval score and SQL ordering is already the exact selection order.
        # Use an exact database count plus an ordered page instead of transferring
        # and materializing the entire history in Python.
        exact_ordered_history = include_all_history and not terms and not temporal_window
        query_scan = bool(terms or temporal_window or include_all_history or source_offset > 0)
        candidate_count = 0
        ranked_items: list[tuple[int, int, dict[str, Any]]] = []
        ranked_limit = limit if exact_ordered_history else source_offset + limit
        if exact_ordered_history:
            # Return the exact per-source count with the ordered page in one
            # database round-trip. Appointment preparation reads every source;
            # a separate COUNT query per table was adding avoidable latency.
            count_statement = None
            scan_statement = statement.add_columns(
                func.count().over().label("_lcr_candidate_count")
            ).offset(source_offset).limit(limit + 1)
        else:
            count_statement = None
            scan_statement = statement if query_scan else statement.limit(limit + 1)
        source_query_failed = False
        expanded_only = [term for term in terms if term not in direct_terms]
        rows_seen = 0
        try:
            # Isolate each source query in a savepoint. A missing table, bad
            # source-specific column, or transient query failure must not turn
            # every other successfully retrieved care category into a failure.
            with db.begin_nested():
                if count_statement is not None:
                    candidate_count = int(db.execute(count_statement).scalar_one() or 0)
                result = db.execute(
                    scan_statement.execution_options(stream_results=True)
                ).mappings().yield_per(500)
                for row in result:
                    rows_seen += 1
                    row_values = dict(row)
                    if exact_ordered_history:
                        candidate_count = int(row_values.pop("_lcr_candidate_count", candidate_count) or 0)
                    else:
                        candidate_count += 1
                    if not query_scan and candidate_count > limit:
                        continue
                    item = {key: _safe_value(value, key) for key, value in row_values.items()}
                    if source_name == "document_extractions":
                        item["extraction_metadata"] = _document_extraction_metadata(item.pop("candidates", None), item.get("source_text"))
                    if source_name in {"appointment_workspaces", "appointment_workspace_revisions"}:
                        item["payload"] = _safe_appointment_workspace_payload(item.get("payload"))
                    information_status, interpretation_limit = _information_status(source_name, item)
                    item["information_status"] = information_status
                    item["interpretation_limit"] = interpretation_limit
                    score = (
                        _retrieval_score(item, query or "", direct_terms, expanded_only)
                        if direct_terms else 0
                    )
                    # The SQL ordering is deterministic. Python's stable sort retains
                    # that order for ties while prioritising exact/direct query matches.
                    ranked_items.append((score, rows_seen if exact_ordered_history else candidate_count, item))
                    if len(ranked_items) > ranked_limit:
                        ranked_items.sort(key=lambda entry: (entry[0], -entry[1]), reverse=True)
                        del ranked_items[ranked_limit:]
                if exact_ordered_history and source_offset > 0 and rows_seen == 0:
                    # An offset beyond the final row returns no window-count
                    # value. Resolve that uncommon page with a count fallback.
                    fallback_count = statement.with_only_columns(func.count()).order_by(None).limit(None).offset(None)
                    candidate_count = int(db.execute(fallback_count).scalar_one() or 0)
        except SQLAlchemyError:
            source_query_failed = True

        ranked_items.sort(key=lambda entry: (entry[0], -entry[1]), reverse=True)
        items = (
            [entry[2] for entry in ranked_items[:limit]]
            if exact_ordered_history
            else [entry[2] for entry in ranked_items[source_offset:source_offset + limit]]
        )
        if source_query_failed:
            sources[source_name] = {
                "status": "partial" if items else "failed",
                "table": table_name,
                "items": items,
                "returned_count": len(items),
                "candidate_count": candidate_count,
                "candidate_scan_complete": False,
                "returned_history_complete": False,
                "has_more": True,
                "limit": limit,
                "offset": source_offset,
                "next_offset": source_offset + len(items),
                "error_code": "source_query_failed",
                "selection": "incomplete_source_read",
                "interpretation_limit": "This care-information source could not be fully retrieved. An incomplete result must not be treated as an empty or complete history.",
            }
            continue

        has_more = candidate_count > source_offset + len(items)
        next_offset = source_offset + len(items) if has_more else None
        sources[source_name] = {
            "status": "available" if items else "empty",
            "table": table_name,
            "items": items,
            "returned_count": len(items),
            "candidate_count": candidate_count,
            "candidate_scan_complete": query_scan,
            # A completed database scan may still return only a ranked page.
            "returned_history_complete": bool(query_scan and source_offset == 0 and not has_more),
            "has_more": has_more,
            "limit": limit,
            "offset": source_offset,
            "next_offset": next_offset,
            "selection": (
                "full_history_ranked_page" if include_all_history and direct_terms else
                "full_history_exact_count_ordered_page" if exact_ordered_history else
                "ranked_query_matches_page" if query_scan and direct_terms else
                "complete_temporal_window_page" if temporal_window else
                "recent_records_page"
            ),
        }

    # Attach explicit, person-scoped evidence links to selected care facts.
    # Without this join, the fact text could be retrieved while its originating
    # evidence remained invisible to the Companion and downstream briefings.
    fact_items = sources.get("facts", {}).get("items", [])
    fact_link_table = metadata.tables.get("care_fact_evidence")
    evidence_table_for_facts = metadata.tables.get("evidence")
    if fact_items and fact_link_table is not None and evidence_table_for_facts is not None:
        fact_ids = [
            str(item["id"]) for item in fact_items
            if isinstance(item, dict) and item.get("id")
        ]
        link_columns = [
            fact_link_table.c[name]
            for name in ("fact_id", "evidence_id", "evidence_role", "source_span")
            if name in fact_link_table.c
        ]
        if fact_ids and {"person_id", "fact_id", "evidence_id"}.issubset(fact_link_table.c.keys()):
            valid_links = db.execute(
                select(*link_columns).select_from(
                    fact_link_table.join(
                        evidence_table_for_facts,
                        and_(
                            fact_link_table.c.evidence_id == evidence_table_for_facts.c.id,
                            evidence_table_for_facts.c.person_id == str(person_id),
                        ),
                    )
                ).where(
                    fact_link_table.c.person_id == str(person_id),
                    fact_link_table.c.fact_id.in_(fact_ids),
                ).order_by(
                    fact_link_table.c.fact_id.asc(),
                    fact_link_table.c.evidence_id.asc(),
                )
            ).mappings().all()
            links_by_fact: dict[str, list[dict[str, Any]]] = {}
            for link in valid_links:
                fact_key = str(link["fact_id"])
                links_by_fact.setdefault(fact_key, []).append({
                    "evidence_id": str(link["evidence_id"]),
                    "evidence_role": link.get("evidence_role"),
                    "source_span": link.get("source_span"),
                    "integrity_status": "verified_person_scoped",
                })
            for item in fact_items:
                links = links_by_fact.get(str(item.get("id")), [])
                item["evidence_links"] = links
                item["evidence_ids"] = [link["evidence_id"] for link in links]
                item["evidence_link_status"] = (
                    "verified_person_scoped_links" if links else "no_verified_linked_evidence"
                )
    elif fact_items:
        for item in fact_items:
            item["evidence_links"] = []
            item["evidence_ids"] = []
            item["evidence_link_status"] = "lineage_schema_unavailable"

    evidence_lineage: dict[str, Any] = {
        "status": "not_assessed",
        "evidence_ids_considered": [],
        "groups": [],
        "relationship_records_examined": 0,
        "relationship_history_bounded": True,
        "interpretation_limit": (
            "Source lineage is based only on explicit parent/source references and persisted "
            "reviewed relationships. Text similarity, shared vocabulary, or model repetition "
            "does not establish common origin or independent corroboration."
        ),
    }
    # Seed lineage from every evidence reference present in the bounded LCR
    # projection, not only the most recent evidence rows. This lets a visible
    # event/decision/appointment/handoff reference retain the provenance of an
    # older source that is outside the visible evidence page.
    selected_ids: set[str] = set()

    def collect_evidence_ids(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                if child_key in {"evidence_id", "source_evidence_id", "related_evidence_id"}:
                    if isinstance(child_value, (str, int)) and str(child_value).strip():
                        selected_ids.add(str(child_value).strip())
                elif child_key == "evidence_ids":
                    if isinstance(child_value, (list, tuple, set)):
                        selected_ids.update(
                            str(item).strip() for item in child_value
                            if isinstance(item, (str, int)) and str(item).strip()
                        )
                    elif isinstance(child_value, str):
                        parsed = _safe_value(child_value, "evidence_ids")
                        if isinstance(parsed, list):
                            selected_ids.update(
                                str(item).strip() for item in parsed
                                if isinstance(item, (str, int)) and str(item).strip()
                            )
                collect_evidence_ids(child_value, child_key)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect_evidence_ids(child, key)

    collect_evidence_ids(sources)
    evidence_items = sources.get("evidence", {}).get("items", [])
    selected_ids.update(
        str(item.get("id")).strip() for item in evidence_items
        if isinstance(item, dict) and item.get("id")
    )

    relationship_table = metadata.tables.get("evidence_source_relationships")
    evidence_table = metadata.tables.get("evidence")
    if selected_ids and relationship_table is not None and evidence_table is not None:
        from app.services.evidence_lineage import (
            load_evidence_lineage_scope,
            summarize_evidence_lineage,
        )
        lineage_scope = load_evidence_lineage_scope(db, str(person_id), selected_ids)
        lineage_summary = summarize_evidence_lineage(
            lineage_scope["evidence_rows"],
            lineage_scope["relationships"],
            selected_ids,
        )
        evidence_lineage.update({
            **lineage_summary,
            "status": "assessed_bounded_subset",
            "relationship_records_examined": len(lineage_scope["relationships"]),
            "relationship_history_bounded": bool(lineage_scope["has_more"]),
            "scope_truncated": bool(lineage_scope["truncated"]),
            "lineage_nodes_examined": len(lineage_scope["evidence_rows"]),
            "interpretation_limit": (
                lineage_summary["interpretation_limit"]
                + " Retrieval is person-scoped and bounded; unreturned lineage is unknown, not absent."
            ),
        })
    elif selected_ids:
        evidence_lineage["status"] = "unavailable_schema"
    # Appointment workspaces are caregiver-authored snapshots. Match the
    # existing workspace API's conservative freshness watermark so stale visit
    # preparation cannot be presented as current after the LCR has changed.
    workspace_source = sources.get("appointment_workspaces")
    if workspace_source and workspace_source.get("items"):
        latest_care_change = None
        freshness_available = True
        try:
            latest_care_change = db.query(func.max(CareStateRevision.changed_at)).filter(
                CareStateRevision.person_id == str(person_id)
            ).scalar()
        except Exception:
            freshness_available = False
        for workspace in workspace_source["items"]:
            updated_value = workspace.get("updated_at")
            try:
                updated_at = (
                    datetime.fromisoformat(str(updated_value).replace("Z", "+00:00"))
                    if updated_value else None
                )
                if updated_at is not None and updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                if latest_care_change is not None and latest_care_change.tzinfo is None:
                    latest_care_change = latest_care_change.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                updated_at = None
            if not freshness_available or updated_at is None:
                workspace["source_freshness"] = {
                    "status": "unknown",
                    "reason": "care_state_freshness_check_unavailable",
                    "interpretation_limit": "Currentness could not be confirmed from the saved care-change history.",
                }
            elif latest_care_change is not None and latest_care_change > updated_at:
                workspace["source_freshness"] = {
                    "status": "stale",
                    "latest_care_change_at": latest_care_change.isoformat(),
                    "reason": "care_record_changed_after_workspace_save",
                    "interpretation_limit": "At least one care-record change was saved after this appointment workspace.",
                }
            else:
                workspace["source_freshness"] = {
                    "status": "current",
                    "latest_care_change_at": None,
                    "reason": None,
                    "interpretation_limit": "No later person-scoped care-record change was found. This watermark does not replace exact source-dependency verification.",
                }

    # A source that cannot be searched is not an empty source. It must
    # reduce retrieval completeness just like a failed or partial source read.
    failed_sources = sorted(
        name for name, source in sources.items()
        if source.get("status") in {"failed", "partial", "unsearchable"}
    )
    failed_required_sources = sorted(
        name for name in failed_sources if SOURCE_SPECS.get(name, {}).get("required")
    )
    failed_optional_sources = sorted(
        name for name in failed_sources if not SOURCE_SPECS.get(name, {}).get("required")
    )
    schema_status = "complete" if not missing_required and not missing_optional and not failed_sources else "partial"
    return {
        "person_id": str(person_id),
        "as_of": datetime.now().astimezone().isoformat(),
        "read_only": True,
        "requested_sources": sorted(only_sources) if only_sources is not None else sorted(SOURCE_SPECS),
        "history_coverage": (
            "partial_source_history" if failed_sources
            else "paginated_history_page" if any(source.get("offset", 0) > 0 for source in sources.values())
            else "complete_history_scan_ranked_subset" if include_all_history
            else "query_scanned_ranked_subset" if terms or temporal_window
            else "bounded_recent_subset"
        ),
        "failed_sources": failed_sources,
        "failed_required_sources": failed_required_sources,
        "failed_optional_sources": failed_optional_sources,
        "source_offsets": {
            name: source.get("offset", 0) for name, source in sources.items()
            if source.get("offset", 0) > 0
        },
                # A scan is complete when every source whose schema is available was
        # exhausted. Schema gaps are reported independently below; they must not
        # erase proof that the available source history was fully scanned.
        "retrieval_scan_complete": (
            bool(terms or temporal_window or include_all_history)
            and not failed_sources
            and any(source.get("status") in {"available", "empty"} for source in sources.values())
            and all(
                source.get("candidate_scan_complete") is True
                for source in sources.values()
                if source.get("status") in {"available", "empty"}
            )
        ),
        "retrieval_projection_complete": (
            bool(terms or temporal_window or include_all_history)
            and not missing_required
            and not missing_optional
            and not failed_sources
            and all(
                source.get("candidate_scan_complete") is True
                and source.get("offset", 0) == 0
                and source.get("has_more") is False
                for source in sources.values()
                if source.get("status") in {"available", "empty"}
            )
        ),
        "query_applied": bool(terms),
        "retrieval_strategy": ("person_scoped_exact_count_ordered_history" if include_all_history and not terms and not temporal_window else "person_scoped_full_source_scan") if include_all_history else ("hybrid_lexical_semantic_temporal" if query or temporal_window else "bounded_recent_history"),
        "temporal_window": temporal_window,
        "schema_status": schema_status,
        "missing_required_sources": missing_required,
        "missing_optional_sources": missing_optional,
        "sources": sources,
        "evidence_lineage": evidence_lineage,
    }


def format_lcr_context_for_prompt(context: dict[str, Any], *, max_chars: int = 14000) -> str:
    """Serialize bounded LCR context fairly across source types.

    Source metadata is emitted before record details, then records are added in
    round-robin order. A large event history therefore cannot consume the entire
    prompt budget before evidence, corrections, conflicts, decisions, or open
    loops are represented. Any records omitted by the prompt budget are disclosed.
    """
    max_chars = max(256, int(max_chars))
    source_caveats = []
    for source_name, source in context.get("sources", {}).items():
        for item in source.get("items", []):
            source_context = item.get("source_context")
            if not isinstance(source_context, dict):
                continue
            candidate = source_context.get("document_candidate")
            candidate = candidate if isinstance(candidate, dict) else {}
            authority = source_context.get("authority")
            negation_status = candidate.get("negation_status")
            if authority or negation_status:
                caveat = {
                    "source_type": source_name,
                    "source_id": str(item.get("id") or "")[:120],
                    "authority": str(authority)[:120] if authority is not None else None,
                    "negation_status": str(negation_status)[:60] if negation_status is not None else None,
                }
                page_number = candidate.get("page_number")
                if isinstance(page_number, int) and not isinstance(page_number, bool) and page_number > 0:
                    caveat["page_number"] = page_number
                source_caveats.append(caveat)
    # Preserve source-page and authority details for accepted care assertions
    # before less decision-relevant source notes. Otherwise a large evidence-review
    # history can consume the bounded caveat list and hide the page of the very
    # document claim the Companion is being asked to explain.
    # Prioritize assertion caveats with page-level document provenance, then
    # preserve retrieval order within each priority group. This keeps the source
    # page for accepted document claims visible under the bounded caveat budget.
    source_caveats.sort(
        key=lambda caveat: (
            caveat.get("source_type") != "assertions",
            caveat.get("source_type") == "assertions" and not isinstance(caveat.get("page_number"), int),
        )
    )
    header = (
        "LCR context metadata: "
        + json.dumps({
            "schema_status": context.get("schema_status"),
            "source_limits": [
                {
                    "source_type": name,
                    "status": source.get("status"),
                    "interpretation_limit": source.get("interpretation_limit"),
                }
                for name, source in context.get("sources", {}).items()
                if source.get("status") in {"failed", "partial", "unsearchable"}
                and source.get("interpretation_limit")
            ][:20],
            "source_caveats": source_caveats[:20],
            "history_coverage": context.get("history_coverage"),
            "retrieval_scan_complete": context.get("retrieval_scan_complete", False),
            "retrieval_projection_complete": context.get("retrieval_projection_complete", False),
            "missing_required_sources": context.get("missing_required_sources", []),
            "missing_optional_sources": context.get("missing_optional_sources", []),
            "evidence_lineage": context.get("evidence_lineage", {
                "status": "not_assessed",
                "interpretation_limit": "No source-lineage assessment was supplied.",
            }),
        }, ensure_ascii=False, separators=(",", ":"))
    )

    prepared_sources = []
    for source_name, source in context.get("sources", {}).items():
        items = [
            json.dumps(
                {
                    "source_type": source_name,
                    "source_id": item.get("id"),
                    "record": item,
                },
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            )
            for item in source.get("items", [])
        ]
        section = (
            f"[{source_name}; status={source.get('status')}; "
            f"returned={source.get('returned_count', 0)}; "
            f"candidates={source.get('candidate_count', 'not_counted')}; "
            f"scan_complete={source.get('candidate_scan_complete', False)}; "
            f"has_more={source.get('has_more', False)}]"
        )
        prepared_sources.append({"section": section, "items": items, "next": 0})

    base = header + "".join("\n" + source["section"] for source in prepared_sources)
    truncation_notice = (
        "\n[Context truncated because the safe prompt budget was reached. "
        "Some retrieved records were not included; consult the source coverage "
        "metadata and do not treat this prompt as a complete history.]"
    )

    # Keep the source coverage header even when a caller supplies an unusually
    # small budget. The normal production budget is substantially larger.
    if len(base) + len(truncation_notice) > max_chars:
        shortened = header[: max(0, max_chars - len(truncation_notice))]
        return shortened + truncation_notice[: max_chars - len(shortened)]

    chunks = [base]
    current_length = len(base)
    item_budget = max_chars - len(base) - len(truncation_notice)
    omitted = False

    # Give each available source a chance to contribute before taking a second
    # record from any source. Preserve each source's own item order.
    while True:
        made_progress = False
        for source in prepared_sources:
            index = source["next"]
            if index >= len(source["items"]):
                continue
            line = "\n" + source["items"][index]
            if current_length + len(line) > len(base) + item_budget:
                omitted = True
                continue
            chunks.append(line)
            current_length += len(line)
            source["next"] += 1
            made_progress = True
        if not made_progress:
            break

    if any(source["next"] < len(source["items"]) for source in prepared_sources):
        omitted = True
    if omitted:
        chunks.append(truncation_notice)
    return "".join(chunks)
