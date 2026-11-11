"""Unit tests for source-grounded Qwen capture candidate validation.

These tests exercise the gateway's deterministic acceptance boundary without
pretending that mocked output proves live model quality.
"""
import json

import pytest
from fastapi import HTTPException

from app.services import qwen_gateway


def test_capture_candidates_keep_only_verbatim_source_grounded_observations(monkeypatch):
    source = (
        "Caregiver report: Mum slept badly last night. "
        "She ate half of breakfast this morning."
    )
    model_output = {
        "observations": [
            {
                "subject": "Mum",
                "observation": "Mum slept badly last night.",
                "source_quote": "Mum slept badly last night.",
            },
            {
                "subject": "She",
                "observation": "She ate half of breakfast this morning.",
                "source_quote": "She ate half of breakfast this morning.",
            },
            {
                "subject": "Mum",
                "observation": "Mum has dementia.",
                "source_quote": "Mum has dementia.",
            },
            {
                "subject": "Mum",
                "observation": "Mum slept well last night.",
                "source_quote": "Mum slept badly last night.",
            },
            {
                "subject": "Mum",
                "observation": "The poor sleep was caused by pain.",
                "source_quote": "Mum slept badly last night.",
            },
        ],
        "open_questions": [
            "Was this unusual?",
            "Was breakfast completed?",
            "Is there more context?",
            "This fourth question must be discarded.",
        ],
    }
    captured = {}

    def fake_qwen_chat(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return json.dumps(model_output)

    monkeypatch.setattr(qwen_gateway, "qwen_chat", fake_qwen_chat)

    result = qwen_gateway.extract_review_candidates(
        source,
        source_event_id="event-123",
        source_evidence_id="evidence-456",
    )

    assert len(result["observations"]) == 2
    assert [item["observation"] for item in result["observations"]] == [
        "Mum slept badly last night.",
        "She ate half of breakfast this morning.",
    ]
    assert all(item["review_status"] == "pending_review" for item in result["observations"])
    assert all(item["source_event_id"] == "event-123" for item in result["observations"])
    assert all(item["source_evidence_id"] == "evidence-456" for item in result["observations"])
    assert all(item["source_quote"] in source for item in result["observations"])
    assert len(result["open_questions"]) == 3
    assert captured["kwargs"]["response_format"] == "json"
    assert captured["kwargs"]["options"]["temperature"] == 0


def test_capture_candidates_fail_closed_on_invalid_json(monkeypatch):
    monkeypatch.setattr(qwen_gateway, "qwen_chat", lambda *args, **kwargs: "not-json")

    with pytest.raises(HTTPException) as error:
        qwen_gateway.extract_review_candidates(
            "Caregiver reports poor sleep.",
            source_event_id="event-123",
        )

    assert error.value.status_code == 503
    assert "invalid candidate structure" in error.value.detail


def test_capture_candidates_return_empty_for_blank_source_without_calling_model(monkeypatch):
    monkeypatch.setattr(
        qwen_gateway,
        "qwen_chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("blank source must not invoke Qwen")),
    )

    assert qwen_gateway.extract_review_candidates("   ", source_event_id="event-123") == {
        "observations": [],
        "open_questions": [],
    }
