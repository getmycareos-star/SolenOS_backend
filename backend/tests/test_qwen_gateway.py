import json

import pytest
from fastapi import HTTPException

from app.services import qwen_gateway


def test_candidate_extraction_requires_exact_source_quote(monkeypatch):
    monkeypatch.setattr(
        qwen_gateway,
        "qwen_chat",
        lambda messages, **kwargs: json.dumps({
            "observations": [
                {"subject": "routine", "observation": "The caregiver reports an evening routine.", "source_quote": "An evening routine helped."},
                {"subject": "unsupported", "observation": "The person has a diagnosis.", "source_quote": "A diagnosis was confirmed."},
            ],
            "open_questions": ["Which step was difficult?"],
        }),
    )
    result = qwen_gateway.extract_review_candidates(
        "An evening routine helped.",
        source_event_id="event-1",
        source_evidence_id="evidence-1",
    )
    assert len(result["observations"]) == 1
    candidate = result["observations"][0]
    assert candidate["source_quote"] == "An evening routine helped."
    assert candidate["source_event_id"] == "event-1"
    assert candidate["source_evidence_id"] == "evidence-1"
    assert candidate["review_status"] == "pending_review"
    assert result["open_questions"] == ["Which step was difficult?"]


def test_candidate_extraction_does_not_return_model_json_failures_as_candidates(monkeypatch):
    monkeypatch.setattr(qwen_gateway, "qwen_chat", lambda messages, **kwargs: "not json")
    with pytest.raises(HTTPException) as error:
        qwen_gateway.extract_review_candidates("Caregiver note", source_event_id="event-2")
    assert error.value.status_code == 503


def test_production_sidecar_allows_only_explicit_local_ollama(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:1.5b")
    monkeypatch.delenv("OLLAMA_SIDECAR_ENABLED", raising=False)
    with pytest.raises(HTTPException):
        qwen_gateway.get_ollama_config()

    monkeypatch.setenv("OLLAMA_SIDECAR_ENABLED", "true")
    base, model, timeout = qwen_gateway.get_ollama_config()
    assert base == "http://127.0.0.1:11434"
    assert model == "qwen2.5:1.5b"
    assert timeout >= 5


def test_production_qwen_rejects_public_hosts_even_with_sidecar_enabled(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://example.com")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:1.5b")
    monkeypatch.setenv("OLLAMA_SIDECAR_ENABLED", "true")
    with pytest.raises(HTTPException):
        qwen_gateway.get_ollama_config()
