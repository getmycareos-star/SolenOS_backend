from __future__ import annotations

import json
from contextlib import contextmanager

from app.services import qwen_gateway


def test_installation_state_is_machine_readable_and_restricts_failure_reason(tmp_path, monkeypatch):
    state_file = tmp_path / "qwen-state.json"
    monkeypatch.setenv("QWEN_INSTALL_STATE_FILE", str(state_file))
    state_file.write_text(json.dumps({
        "state": "failed",
        "model": "qwen2.5:0.5b",
        "updated_at": "2026-10-31T00:00:00+00:00",
        "failure_reason": "ollama_pull_failed",
    }), encoding="utf-8")

    status = qwen_gateway.qwen_installation_status()
    assert status == {
        "state": "failed",
        "model": "qwen2.5:0.5b",
        "updated_at": "2026-10-31T00:00:00+00:00",
        "failure_reason": "ollama_pull_failed",
    }


def test_missing_installation_state_defaults_to_not_started(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_INSTALL_STATE_FILE", str(tmp_path / "missing.json"))
    assert qwen_gateway.qwen_installation_status()["state"] == "not_started"


def test_readiness_requires_installed_model_and_real_inference(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"models":[{"name":"qwen2.5:0.5b"}]}'

    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama", "qwen2.5:0.5b", 20.0))
    monkeypatch.setattr(qwen_gateway, "qwen_installation_status", lambda: {"state": "installed"})
    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    calls = []
    monkeypatch.setattr(qwen_gateway, "qwen_chat", lambda *args, **kwargs: calls.append((args, kwargs)) or "READY")
    qwen_gateway._READINESS_CACHE.update({"checked_at": 0.0, "result": None})

    result = qwen_gateway.qwen_readiness_status(force=True)
    assert result["available"] is True
    assert result["state"] == "ready"
    assert len(calls) == 1
    assert calls[0][1]["timeout"] <= 12.0


def test_readiness_fails_when_configured_model_is_not_installed(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"models":[{"name":"another-model"}]}'

    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama", "qwen2.5:0.5b", 20.0))
    monkeypatch.setattr(qwen_gateway, "qwen_installation_status", lambda: {"state": "downloading"})
    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    monkeypatch.setattr(qwen_gateway, "qwen_chat", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("inference must not run")))
    qwen_gateway._READINESS_CACHE.update({"checked_at": 0.0, "result": None})

    result = qwen_gateway.qwen_readiness_status(force=True)
    assert result["available"] is False
    assert result["state"] == "model_not_installed"
    assert result["failure_reason"] == "configured_model_missing"



def test_assistant_status_does_not_claim_operational_when_only_inventory_is_available(monkeypatch):
    from app.api import collaboration

    monkeypatch.setattr(collaboration, "qwen_runtime_status", lambda: {
        "available": True,
        "installation": {"state": "installed"},
    })
    monkeypatch.setattr(collaboration, "qwen_readiness_status", lambda: {
        "available": False,
        "state": "unavailable",
        "failure_reason": "inference_timeout",
    })

    status = collaboration.qwen_assistant_status()
    assert status["available"] is False
    assert status["operational"] is False
    assert status["installation"]["state"] == "installed"
    assert status["readiness"]["failure_reason"] == "inference_timeout"


def test_production_evaluation_has_five_independent_synthetic_acceptance_criteria():
    from app.api.collaboration import _production_qwen_evaluation_cases

    cases = _production_qwen_evaluation_cases()
    assert {case["criterion"] for case in cases} == {
        "contradiction_preservation",
        "reporter_attribution",
        "event_chronology",
        "uncertainty",
        "medication_safety",
    }
    assert len(cases) == 5
    assert all("Synthetic record:" in case["context"] for case in cases)


def test_readiness_and_production_evaluation_are_public_to_session_middleware_only():
    from app.main import PUBLIC_V1_ENDPOINTS

    assert ("/api/v1/assistant/readiness", "GET") in PUBLIC_V1_ENDPOINTS
    assert ("/api/v1/assistant/evaluate-production", "POST") in PUBLIC_V1_ENDPOINTS



def test_companion_fails_closed_with_explicit_machine_readable_unavailable_reason(monkeypatch):
    from fastapi import HTTPException
    from app.api import collaboration

    monkeypatch.setattr(collaboration, "qwen_readiness_status", lambda: {
        "available": False,
        "state": "unavailable",
        "model": "qwen2.5:0.5b",
        "failure_reason": "inference_timeout",
        "error_category": "inference_timeout",
    })
    try:
        collaboration._ensure_companion_available()
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail["code"] == "companion_ai_unavailable"
        assert exc.detail["reason"] == "inference_timeout"
        assert "temporarily unavailable" in exc.detail["message"]
    else:
        raise AssertionError("Companion must fail closed when Qwen readiness fails")
