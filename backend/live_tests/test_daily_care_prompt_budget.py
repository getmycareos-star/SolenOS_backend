import os

import pytest

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("TEST_DATABASE_URL") or ""
if not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://")):
    pytest.skip("Daily Care prompt-budget tests require PostgreSQL configuration", allow_module_level=True)

import json

import pytest
from fastapi import HTTPException

from app.api.daily_care_intelligence import (
    DAILY_CARE_MAX_OUTPUT_TOKENS,
    DAILY_CARE_PROMPT_CONTEXT_BUDGET_CHARS,
    _daily_care_lcr_context_summary,
    _fit_daily_care_prompt_context,
)


def test_daily_care_prompt_context_preserves_source_identity_and_marks_truncation():
    records = [
        {
            "source_type": "care_event",
            "source_id": f"event-{index}",
            "occurred_at": f"2026-11-{20-index:02d}T09:00:00Z",
            "field": "sleep_observation",
            "asserted_value": f"Caregiver report {index}",
            "epistemic_state": "reported",
            "assertion_status": "reported",
            "source_text": ("Caregiver source wording. " * 90) + str(index),
        }
        for index in range(10)
    ]
    lcr_context = {
        "schema_status": "partial",
        "retrieval_completeness": "partial",
        "answer_restriction": "bounded_history",
        "missing_required_sources": ["documents"],
        "failed_sources": [],
        "failed_optional_sources": ["appointments"],
        "sources": {
            "care_events": {"status": "complete", "count": 10, "has_more": True},
            "documents": {"status": "unavailable", "count": 0, "has_more": False, "error_code": "source_unavailable"},
        },
        "rows": [{"raw_text": "This large source row must not be copied into the model summary."}],
    }

    prompt, selected, omitted, truncated = _fit_daily_care_prompt_context(
        "What changed in sleep?",
        records,
        lcr_context,
        True,
        matching_records_in_database=10,
    )

    assert len(json.dumps(prompt, ensure_ascii=False)) <= DAILY_CARE_PROMPT_CONTEXT_BUDGET_CHARS
    assert selected
    assert selected[0]["source_id"] == "event-0"
    assert selected[0]["occurred_at"].startswith("2026-11-20")
    assert selected[0]["epistemic_state"] == "reported"
    assert selected[0]["source_excerpt_truncated"] is True
    assert omitted == 10 - len(selected)
    assert truncated is True
    assert prompt["retrieval_metadata"]["matching_records_in_database"] == 10
    assert prompt["retrieval_metadata"]["records_omitted_from_model"] == omitted
    assert prompt["retrieval_metadata"]["selection_truncated"] is True
    assert prompt["retrieval_metadata"]["source_text_truncated"] is True
    assert prompt["retrieval_metadata"]["lcr_context"]["missing_required_sources"] == ["documents"]
    assert "rows" not in prompt["retrieval_metadata"]["lcr_context"]


def test_daily_care_lcr_summary_keeps_coverage_not_full_source_rows():
    summary = _daily_care_lcr_context_summary({
        "schema_status": "partial",
        "retrieval_completeness": "partial",
        "answer_restriction": "partial_history_only",
        "failed_sources": ["documents"],
        "sources": {
            "care_events": {"status": "complete", "count": 4, "has_more": True, "records": [{"text": "private body"}]},
        },
        "records": [{"text": "must not enter model metadata"}],
    })

    assert summary["schema_status"] == "partial"
    assert summary["sources"]["care_events"] == {
        "status": "complete",
        "count": 4,
        "has_more": True,
    }
    assert "records" not in summary
    assert "private body" not in json.dumps(summary)


def test_daily_care_rejects_oversized_question_instead_of_silently_truncating():
    with pytest.raises(HTTPException) as error:
        _fit_daily_care_prompt_context(
            "question " * 200,
            [{"source_type": "care_event", "source_id": "event-1"}],
            {},
            False,
        )

    assert error.value.status_code == 413
    assert error.value.detail["code"] == "daily_care_question_context_budget_exceeded"


def test_daily_care_output_ceiling_preserves_room_for_retrieved_evidence():
    assert DAILY_CARE_MAX_OUTPUT_TOKENS == 384
    assert DAILY_CARE_PROMPT_CONTEXT_BUDGET_CHARS == 2200
