from datetime import datetime, timezone
import json
import pytest
from fastapi import HTTPException

from app.services import delta_engine


class FakeResponse:
    def raise_for_status(self):
        return None
    def json(self):
        return {"message": {"content": json.dumps({
            "event_summary": "Medication review",
            "medications": [{"change_type": "dose_changed", "name": "metformin", "previous": "500 mg", "current": "1000 mg", "evidence_excerpt": "Increase metformin to 1000 mg", "certainty": "supported"}],
            "conditions": [], "symptoms": [], "tests": [{"name": "MRI", "status": "ordered", "ordered_date": "2026-10-22", "result_present": False, "evidence_excerpt": "MRI ordered"}],
            "follow_ups": [{"action": "Book follow-up", "status": "pending", "due_date": None, "owner": None, "evidence_excerpt": "follow-up in 2 weeks"}],
            "contradictions": [], "clinical_gaps": [{"gap": "MRI result not in supplied record", "why_it_matters": "Result not available", "next_question": "Has the MRI been completed?", "evidence_excerpt": "MRI ordered"}],
            "caregiver_summary": "A medication dose change was reported. The MRI result is not present in this event.",
            "clinician_summary": "Reported metformin dose change; MRI result not present in supplied event."
        })}}


def test_invalid_model_json_fails_closed(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        delta_engine._parse_model_json("not json")
    assert exc.value.status_code == 502


def test_missing_local_model_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    with pytest.raises(HTTPException) as exc:
        delta_engine._local_ollama_url()
    assert exc.value.status_code == 503


def test_local_model_config_rejects_loopback_in_production(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    with pytest.raises(HTTPException):
        delta_engine._local_ollama_url()


def test_delta_contract_keeps_missing_result_and_follow_up_open(monkeypatch):
    parsed = delta_engine._parse_model_json(FakeResponse().json()["message"]["content"])
    assert parsed["tests"][0]["result_present"] is False
    assert parsed["follow_ups"][0]["status"] == "pending"
    assert parsed["clinical_gaps"][0]["next_question"] == "Has the MRI been completed?"
