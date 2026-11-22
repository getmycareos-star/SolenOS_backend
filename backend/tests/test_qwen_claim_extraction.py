import json

import pytest
from fastapi import HTTPException

from app.api import collaboration


def _request():
    return collaboration.QwenClaimExtractionRequest(
        evidence_id="evidence-1",
        raw_text="Mom fell yesterday. She seemed tired.",
    )


def test_qwen_claim_extraction_uses_two_separate_model_calls_and_validates_source_spans(monkeypatch):
    calls = []
    replies = [
        {"claims": [{
            "claim_text": "Mom fell yesterday",
            "entity_type": "event",
            "event_type": "fall_observed",
            "confidence_tag": "reported",
            "source_span": {"start_offset": 0, "end_offset": 19, "text": "Mom fell yesterday"},
        }]},
        {"claims": [{
            "claim_text": "She seemed tired",
            "entity_type": "symptom",
            "event_type": "fatigue_reported",
            "confidence_tag": "reported",
            "source_span": {"start_offset": 21, "end_offset": 37, "text": "She seemed tired"},
        }]},
    ]

    monkeypatch.setattr(collaboration, "_scope", lambda *_args: ("person-1", "caregiver-1"))

    def fake_qwen(messages, **kwargs):
        calls.append((messages, kwargs))
        return json.dumps(replies[len(calls) - 1])

    monkeypatch.setattr(collaboration, "qwen_chat", fake_qwen)
    result = collaboration.qwen_claim_extraction(_request(), request=None, db=None)

    assert len(calls) == 2
    assert "run A" in calls[0][0][0]["content"]
    assert "run B" in calls[1][0][0]["content"]
    assert result["run_a"]["claims"][0]["source_span"]["text"] == "Mom fell yesterday"
    assert result["run_b"]["claims"][0]["source_span"]["text"] == "She seemed tired"
    assert result["provider"] == "private_ollama_qwen"
    assert result["written_to_lcr"] is False
    assert result["review_required"] is True


def test_qwen_claim_extraction_nulls_unverifiable_source_spans(monkeypatch):
    monkeypatch.setattr(collaboration, "_scope", lambda *_args: ("person-1", "caregiver-1"))
    replies = iter([
        {"claims": [{
            "claim_text": "Mom fell",
            "entity_type": "event",
            "event_type": "fall_observed",
            "confidence_tag": "reported",
            "source_span": {"start_offset": 0, "end_offset": 8, "text": "not exact"},
        }]},
        {"claims": []},
    ])
    monkeypatch.setattr(collaboration, "qwen_chat", lambda *_args, **_kwargs: json.dumps(next(replies)))

    result = collaboration.qwen_claim_extraction(_request(), request=None, db=None)

    assert result["run_a"]["claims"][0]["source_span"] is None
    assert result["run_b"]["claims"] == []


def test_qwen_claim_extraction_fails_closed_on_malformed_model_output(monkeypatch):
    monkeypatch.setattr(collaboration, "_scope", lambda *_args: ("person-1", "caregiver-1"))
    monkeypatch.setattr(collaboration, "qwen_chat", lambda *_args, **_kwargs: "not-json")

    with pytest.raises(HTTPException) as error:
        collaboration.qwen_claim_extraction(_request(), request=None, db=None)

    assert error.value.status_code == 503
