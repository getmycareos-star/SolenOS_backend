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
    assert "SOLENOS_WORKFLOW_ID: care_claim_extraction." in captured["messages"][0]["content"]


def test_document_candidate_extraction_activates_document_specialist(monkeypatch):
    captured = {}

    def fake_qwen_chat(messages, **kwargs):
        captured["messages"] = messages
        return json.dumps({"observations": [], "open_questions": []})

    monkeypatch.setattr(qwen_gateway, "qwen_chat", fake_qwen_chat)
    result = qwen_gateway.extract_review_candidates(
        "The discharge summary records a historical medication list.",
        source_event_id="",
        source_evidence_id="evidence-document",
        workflow_id="document_candidate_extraction",
    )

    assert result["observations"] == []
    assert "SOLENOS_WORKFLOW_ID: document_candidate_extraction." in captured["messages"][0]["content"]


def test_daily_capture_extraction_activates_daily_capture_workflow_specialist(monkeypatch):
    captured = {}

    def fake_qwen_chat(messages, **kwargs):
        captured["messages"] = messages
        return json.dumps({"observations": [], "open_questions": []})

    monkeypatch.setattr(qwen_gateway, "qwen_chat", fake_qwen_chat)
    result = qwen_gateway.extract_review_candidates(
        "Caregiver reported that Mum left most of breakfast untouched.",
        source_event_id="daily-event",
        source_evidence_id="daily-evidence",
        workflow_id="daily_capture",
    )

    assert result["observations"] == []
    assert "SOLENOS_WORKFLOW_ID: daily_capture." in captured["messages"][0]["content"]


def test_candidate_extraction_rejects_unknown_workflow_before_model_call(monkeypatch):
    monkeypatch.setattr(
        qwen_gateway,
        "qwen_chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unsupported workflow must not invoke model")),
    )
    with pytest.raises(ValueError, match="unsupported_candidate_extraction_workflow"):
        qwen_gateway.extract_review_candidates(
            "Caregiver reports poor sleep.",
            source_event_id="event-unknown",
            workflow_id="invented_workflow",
        )


def test_capture_candidates_reject_oversized_source_without_truncating(monkeypatch):
    monkeypatch.setattr(
        qwen_gateway,
        "qwen_chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("oversized source must not invoke Qwen")),
    )
    with pytest.raises(HTTPException) as error:
        qwen_gateway.extract_review_candidates(
            "care evidence " * 1000,
            source_event_id="event-too-large",
        )
    assert error.value.status_code == 413
    assert error.value.detail["code"] == "candidate_source_exceeds_limit"


def test_companion_update_rejects_oversized_message_without_truncating(monkeypatch):
    monkeypatch.setattr(
        qwen_gateway,
        "qwen_chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("oversized message must not invoke Qwen")),
    )
    with pytest.raises(HTTPException) as error:
        qwen_gateway.extract_companion_care_candidates("care information " * 300)
    assert error.value.status_code == 413
    assert error.value.detail["code"] == "companion_update_source_exceeds_limit"


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


def test_explicit_diagnosis_cause_and_medication_transitions_remain_reviewable(monkeypatch):
    source = (
        "The discharge summary states the patient was previously diagnosed with Parkinson's disease. "
        "It records that reduced intake was caused by nausea. "
        "The medication dosage changed from 500 mg once daily to 1000 mg twice daily on 2026-10-16. "
        "Current medication differs from discharge medication."
    )
    model_output = {
        "observations": [
            {
                "subject": "Parkinson's disease",
                "observation": "previously diagnosed with Parkinson's disease",
                "source_quote": "The discharge summary states the patient was previously diagnosed with Parkinson's disease.",
            },
            {
                "subject": "reduced intake",
                "observation": "reduced intake was caused by nausea",
                "source_quote": "It records that reduced intake was caused by nausea.",
            },
            {
                "subject": "medication",
                "observation": "dosage changed from 500 mg once daily to 1000 mg twice daily",
                "source_quote": "The medication dosage changed from 500 mg once daily to 1000 mg twice daily on 2026-10-16.",
            },
            {
                "subject": "medication",
                "observation": "Current medication differs from discharge medication.",
                "source_quote": "Current medication differs from discharge medication.",
            },
        ],
        "open_questions": [],
    }
    captured = {}

    def fake_qwen_chat(messages, **kwargs):
        captured["messages"] = messages
        return json.dumps(model_output)

    monkeypatch.setattr(qwen_gateway, "qwen_chat", fake_qwen_chat)

    result = qwen_gateway.extract_review_candidates(
        source,
        source_event_id="document-event-123",
        source_evidence_id="document-evidence-456",
    )

    assert len(result["observations"]) == 4
    assert all(item["review_status"] == "pending_review" for item in result["observations"])
    assert all(item["source_quote"] in source for item in result["observations"])
    assert result["observations"][0]["observation"] == "previously diagnosed with Parkinson's disease"
    assert result["observations"][1]["observation"] == "reduced intake was caused by nausea"
    assert result["observations"][2]["observation"] == "dosage changed from 500 mg once daily to 1000 mg twice daily"
    assert result["observations"][3]["observation"] == "Current medication differs from discharge medication."
    prompt = captured["messages"][0]["content"]
    assert "do extract a diagnosis, medication discontinuation, dosage transition" in prompt
    assert "Never turn these statements into a clinical conclusion" in prompt


def test_qwen_quote_expansion_restores_source_attribution_before_review(monkeypatch):
    from app.services.document_candidate_schema import canonical_candidate

    source = "The caregiver reported that the patient slept for six uninterrupted hours on 14 October."
    model_output = {
        "observations": [
            {
                "subject": "the patient",
                "observation": "slept for six uninterrupted hours",
                "source_quote": "the patient slept for six uninterrupted hours",
            }
        ],
        "open_questions": [],
    }
    monkeypatch.setattr(
        qwen_gateway,
        "qwen_chat",
        lambda *args, **kwargs: json.dumps(model_output),
    )
    result = qwen_gateway.extract_review_candidates(
        source,
        source_event_id="event-attribution",
        source_evidence_id="evidence-attribution",
    )
    assert result["observations"][0]["source_quote"] == source
    canonical = canonical_candidate(
        result["observations"][0],
        document_id="document-attribution",
        source_text=source,
    )
    assert canonical["page_number"] == 1
    assert canonical["source_attribution"] == "caregiver_reported"
