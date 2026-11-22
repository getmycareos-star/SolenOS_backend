import json

import pytest
from fastapi import HTTPException

from app.api import collaboration


def _request():
    return collaboration.QwenCareUnderstandingRequest(
        evidence_id="evidence-1",
        raw_text="Mom fell yesterday. Her daughter called the clinic.",
    )


def _allow(monkeypatch):
    monkeypatch.setattr(collaboration, "_scope", lambda *_args: ("person-1", "caregiver-1"))
    monkeypatch.setattr(collaboration, "_require_owned_qwen_source", lambda *_args: None)


def test_care_understanding_returns_source_grounded_review_only_proposal(monkeypatch):
    _allow(monkeypatch)
    model_output = {
        "observations": [{
            "description": "Mom fell yesterday",
            "approximate_time": "yesterday",
            "confidence": "medium",
            "raw_fragment": "Mom fell yesterday",
        }],
        "events": [],
        "decisions": [],
        "outcomes": [],
        "unknowns": [],
        "non_care_facts": [],
        "possible_links": [],
    }
    calls = []
    monkeypatch.setattr(
        collaboration,
        "qwen_chat",
        lambda messages, **kwargs: calls.append((messages, kwargs)) or json.dumps(model_output),
    )

    result = collaboration.qwen_care_understanding(_request(), request=None, db=None)

    assert len(calls) == 1
    assert result["evidence_id"] == "evidence-1"
    assert result["understanding"]["observations"][0]["raw_fragment"] == "Mom fell yesterday"
    assert result["provider"] == "private_ollama_qwen"
    assert result["written_to_lcr"] is False
    assert result["review_required"] is True


def test_care_understanding_rejects_unauthorized_request_before_qwen(monkeypatch):
    monkeypatch.setattr(collaboration, "_scope", lambda *_args: (_ for _ in ()).throw(HTTPException(status_code=403)))
    called = False

    def fake_qwen(*_args, **_kwargs):
        nonlocal called
        called = True
        return "{}"

    monkeypatch.setattr(collaboration, "qwen_chat", fake_qwen)
    with pytest.raises(HTTPException) as error:
        collaboration.qwen_care_understanding(_request(), request=None, db=None)

    assert error.value.status_code == 403
    assert called is False


def test_care_understanding_fails_closed_on_malformed_qwen_output(monkeypatch):
    _allow(monkeypatch)
    monkeypatch.setattr(collaboration, "qwen_chat", lambda *_args, **_kwargs: "not-json")

    with pytest.raises(HTTPException) as error:
        collaboration.qwen_care_understanding(_request(), request=None, db=None)

    assert error.value.status_code == 503


def test_care_understanding_does_not_accept_unverifiable_fragments(monkeypatch):
    _allow(monkeypatch)
    model_output = {
        "observations": [{
            "description": "Invented observation",
            "approximate_time": None,
            "confidence": "high",
            "raw_fragment": "This text is not in the source",
        }],
        "events": [],
        "decisions": [],
        "outcomes": [],
        "unknowns": [],
        "non_care_facts": [],
        "possible_links": [],
    }
    monkeypatch.setattr(collaboration, "qwen_chat", lambda *_args, **_kwargs: json.dumps(model_output))

    result = collaboration.qwen_care_understanding(_request(), request=None, db=None)

    assert result["understanding"]["observations"] == []
