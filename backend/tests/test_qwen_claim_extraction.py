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


def test_claim_extraction_drops_claims_without_exact_source_spans(monkeypatch):
    _allow_source(monkeypatch)
    model_output = {
        "claims": [
            {
                "claim_text": "Mom fell yesterday",
                "entity_type": "event",
                "event_type": "fall_reported",
                "confidence_tag": "reported",
                "source_span": {
                    "start_offset": 0,
                    "end_offset": 18,
                    "text": "Mom fell yesterday",
                },
            },
            {
                "claim_text": "Mom had a severe fall",
                "entity_type": "event",
                "event_type": "fall_reported",
                "confidence_tag": "reported",
                "source_span": None,
            },
            {
                "claim_text": "She seemed tired",
                "entity_type": "symptom",
                "event_type": "fatigue_reported",
                "confidence_tag": "confirmed",
                "source_span": {
                    "start_offset": 20,
                    "end_offset": 36,
                    "text": "She seemed tired",
                },
            },
        ]
    }
    monkeypatch.setattr(collaboration, "qwen_chat", lambda *_args, **_kwargs: json.dumps(model_output))

    result = collaboration.qwen_claim_extraction(_request(), request=None, db=None)

    assert result["run_a"]["claims"] == [
        {
            "claim_text": "Mom fell yesterday",
            "entity_type": "event",
            "event_type": "fall_reported",
            "confidence_tag": "reported",
            "source_span": {
                "start_offset": 0,
                "end_offset": 18,
                "text": "Mom fell yesterday",
            },
        }
    ]
    assert result["run_b"]["claims"] == result["run_a"]["claims"]
    assert result["written_to_lcr"] is False
    assert result["review_required"] is True


def test_claim_extraction_uses_unicode_codepoint_offsets(monkeypatch):
    _allow_source(monkeypatch)
    payload = collaboration.QwenClaimExtractionRequest(
        evidence_id="evidence-1",
        raw_text="💛 Mom fell yesterday",
    )
    model_output = {
        "claims": [{
            "claim_text": "Mom fell yesterday",
            "entity_type": "event",
            "event_type": "fall_reported",
            "confidence_tag": "reported",
            "source_span": {
                "start_offset": 2,
                "end_offset": 20,
                "text": "Mom fell yesterday",
            },
        }]
    }
    monkeypatch.setattr(collaboration, "qwen_chat", lambda *_args, **_kwargs: json.dumps(model_output))

    result = collaboration.qwen_claim_extraction(payload, request=None, db=None)

    assert result["run_a"]["claims"][0]["source_span"] == {
        "start_offset": 2,
        "end_offset": 20,
        "text": "Mom fell yesterday",
    }
    assert result["run_b"]["claims"] == result["run_a"]["claims"]

def test_claim_extraction_rejects_exact_positive_substring_that_omits_negation(monkeypatch):
    _allow_source(monkeypatch)
    source = "No fall occurred yesterday."
    start = source.index("fall occurred yesterday")
    end = start + len("fall occurred yesterday")
    model_output = {
        "claims": [{
            "claim_text": "fall occurred yesterday",
            "entity_type": "event",
            "event_type": "fall_reported",
            "confidence_tag": "reported",
            "source_span": {
                "start_offset": start,
                "end_offset": end,
                "text": "fall occurred yesterday",
            },
        }]
    }
    monkeypatch.setattr(
        collaboration,
        "qwen_chat",
        lambda *_args, **_kwargs: json.dumps(model_output),
    )
    payload = collaboration.QwenClaimExtractionRequest(
        evidence_id="evidence-1",
        raw_text=source,
    )

    result = collaboration.qwen_claim_extraction(payload, request=None, db=None)

    assert result["run_a"]["claims"] == []
    assert result["run_b"]["claims"] == []
    assert result["written_to_lcr"] is False
    assert result["review_required"] is True


def test_claim_extraction_keeps_positive_claim_after_unrelated_negated_clause(monkeypatch):
    _allow_source(monkeypatch)
    source = "No fever was reported, but Mum fell yesterday."
    claim = "Mum fell yesterday"
    start = source.index(claim)
    end = start + len(claim)
    model_output = {
        "claims": [{
            "claim_text": claim,
            "entity_type": "event",
            "event_type": "fall_reported",
            "confidence_tag": "reported",
            "source_span": {
                "start_offset": start,
                "end_offset": end,
                "text": claim,
            },
        }, {
            "claim_text": claim,
            "entity_type": "event",
            "event_type": "fall_reported",
            "confidence_tag": "reported",
            "source_span": {
                "start_offset": start,
                "end_offset": end,
                "text": claim,
            },
        }]
    }
    monkeypatch.setattr(
        collaboration,
        "qwen_chat",
        lambda *_args, **_kwargs: json.dumps(model_output),
    )
    payload = collaboration.QwenClaimExtractionRequest(
        evidence_id="evidence-1",
        raw_text=source,
    )

    result = collaboration.qwen_claim_extraction(payload, request=None, db=None)

    assert len(result["run_a"]["claims"]) == 1
    assert len(result["run_b"]["claims"]) == 1
    assert result["run_a"]["claims"][0]["claim_text"] == claim
    assert result["run_b"]["claims"][0]["claim_text"] == claim

@pytest.mark.parametrize(
    "model_output",
    [
        {"claims": [], "write_to_lcr": True},
        {"claims": [{}] * 21},
    ],
)
def test_claim_extraction_rejects_schema_drift_and_over_limit_output(monkeypatch, model_output):
    _allow_source(monkeypatch)
    monkeypatch.setattr(
        collaboration,
        "qwen_chat",
        lambda *_args, **_kwargs: json.dumps(model_output),
    )

    with pytest.raises(HTTPException) as error:
        collaboration.qwen_claim_extraction(_request(), request=None, db=None)

    assert error.value.status_code == 503


def test_claim_extraction_discards_items_with_unrequested_fields(monkeypatch):
    _allow_source(monkeypatch)
    source = "Mom fell yesterday."
    model_output = {
        "claims": [{
            "claim_text": source,
            "entity_type": "event",
            "event_type": "fall_reported",
            "confidence_tag": "reported",
            "source_span": {
                "start_offset": 0,
                "end_offset": len(source),
                "text": source,
            },
            "write_to_lcr": True,
        }]
    }
    monkeypatch.setattr(
        collaboration,
        "qwen_chat",
        lambda *_args, **_kwargs: json.dumps(model_output),
    )
    payload = collaboration.QwenClaimExtractionRequest(
        evidence_id="evidence-1",
        raw_text=source,
    )

    result = collaboration.qwen_claim_extraction(payload, request=None, db=None)

    assert result["run_a"]["claims"] == []
    assert result["run_b"]["claims"] == []
    assert result["written_to_lcr"] is False

def test_claim_extraction_preserves_source_uncertainty_in_exact_spans(monkeypatch):
    _allow_source(monkeypatch)
    source = "Mum may have pneumonia."
    unsupported_claim = "pneumonia"
    start = source.index(unsupported_claim)
    model_output = {
        "claims": [{
            "claim_text": unsupported_claim,
            "entity_type": "symptom",
            "event_type": "condition_mention",
            "confidence_tag": "inferred",
            "source_span": {
                "start_offset": start,
                "end_offset": start + len(unsupported_claim),
                "text": unsupported_claim,
            },
        }]
    }
    monkeypatch.setattr(
        collaboration,
        "qwen_chat",
        lambda *_args, **_kwargs: json.dumps(model_output),
    )
    payload = collaboration.QwenClaimExtractionRequest(
        evidence_id="evidence-1",
        raw_text=source,
    )

    result = collaboration.qwen_claim_extraction(payload, request=None, db=None)

    assert result["run_a"]["claims"] == []
    assert result["run_b"]["claims"] == []
    assert result["written_to_lcr"] is False


def test_claim_extraction_accepts_uncertainty_when_the_full_source_claim_is_preserved(monkeypatch):
    _allow_source(monkeypatch)
    source = "Mum may have pneumonia."
    model_output = {
        "claims": [{
            "claim_text": source,
            "entity_type": "symptom",
            "event_type": "condition_mention",
            "confidence_tag": "inferred",
            "source_span": {
                "start_offset": 0,
                "end_offset": len(source),
                "text": source,
            },
        }]
    }
    monkeypatch.setattr(
        collaboration,
        "qwen_chat",
        lambda *_args, **_kwargs: json.dumps(model_output),
    )
    payload = collaboration.QwenClaimExtractionRequest(
        evidence_id="evidence-1",
        raw_text=source,
    )

    result = collaboration.qwen_claim_extraction(payload, request=None, db=None)

    assert result["run_a"]["claims"][0]["claim_text"] == source
    assert result["run_b"]["claims"][0]["claim_text"] == source
