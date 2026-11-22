import json

import pytest
from fastapi import HTTPException

from app.api import collaboration


def _request():
    return collaboration.QwenClaimExtractionRequest(
        evidence_id="evidence-1",
        raw_text="Mom fell yesterday. She seemed tired.",
    )


def _allow_source(monkeypatch):
    monkeypatch.setattr(collaboration, "_scope", lambda *_args: ("person-1", "caregiver-1"))
    monkeypatch.setattr(collaboration, "_require_owned_qwen_source", lambda *_args: None)


def test_claim_extraction_runs_two_distinct_qwen_passes_and_keeps_review_boundary(monkeypatch):
    _allow_source(monkeypatch)
    calls = []
    replies = [
        {"claims": [{
            "claim_text": "Mom fell yesterday",
            "entity_type": "event",
            "event_type": "fall_reported",
            "confidence_tag": "reported",
            "source_span": {"start_offset": 0, "end_offset": 18, "text": "Mom fell yesterday"},
        }]},
        {"claims": [{
            "claim_text": "She seemed tired",
            "entity_type": "symptom",
            "event_type": "fatigue_reported",
            "confidence_tag": "reported",
            "source_span": {"start_offset": 20, "end_offset": 36, "text": "She seemed tired"},
        }]},
    ]

    def fake_qwen(messages, **kwargs):
        calls.append((messages, kwargs))
        return json.dumps(replies[len(calls) - 1])

    monkeypatch.setattr(collaboration, "qwen_chat", fake_qwen)
    result = collaboration.qwen_claim_extraction(_request(), request=None, db=None)

    assert len(calls) == 2
    assert "First independent pass" in calls[0][0][0]["content"]
    assert "Second independent pass" in calls[1][0][0]["content"]
    assert result["run_a"]["claims"][0]["source_span"]["text"] == "Mom fell yesterday"
    assert result["run_b"]["claims"][0]["source_span"]["text"] == "She seemed tired"
    assert result["provider"] == "private_ollama_qwen"
    assert result["written_to_lcr"] is False
    assert result["review_required"] is True


def test_claim_extraction_rejects_unauthorized_request_before_model_call(monkeypatch):
    monkeypatch.setattr(collaboration, "_scope", lambda *_args: (_ for _ in ()).throw(HTTPException(status_code=403)))
    called = False

    def fake_qwen(*_args, **_kwargs):
        nonlocal called
        called = True
        return '{"claims":[]}'

    monkeypatch.setattr(collaboration, "qwen_chat", fake_qwen)
    with pytest.raises(HTTPException) as error:
        collaboration.qwen_claim_extraction(_request(), request=None, db=None)

    assert error.value.status_code == 403
    assert called is False


def test_claim_extraction_fails_closed_on_malformed_model_output(monkeypatch):
    _allow_source(monkeypatch)
    monkeypatch.setattr(collaboration, "qwen_chat", lambda *_args, **_kwargs: "not-json")

    with pytest.raises(HTTPException) as error:
        collaboration.qwen_claim_extraction(_request(), request=None, db=None)

    assert error.value.status_code == 503
