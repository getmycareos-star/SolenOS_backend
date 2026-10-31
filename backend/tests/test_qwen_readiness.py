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

    assert ("/api/v1/assistant/installation", "GET") in PUBLIC_V1_ENDPOINTS
    assert ("/api/v1/assistant/readiness", "GET") in PUBLIC_V1_ENDPOINTS
    assert ("/api/v1/assistant/evaluate-production", "POST") in PUBLIC_V1_ENDPOINTS



def test_inference_telemetry_records_metadata_without_prompt_or_reply(monkeypatch, caplog):
    from fastapi import HTTPException
    import logging

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"message":{"content":"synthetic secret reply"}}'

    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama", "qwen2.5:0.5b", 20.0))
    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    caplog.set_level(logging.INFO, logger=qwen_gateway.__name__)

    answer = qwen_gateway.qwen_chat([{"role": "user", "content": "synthetic secret prompt"}])
    assert answer == "synthetic secret reply"
    output = "\n".join(record.getMessage() for record in caplog.records)
    assert '"event":"qwen_inference"' in output
    assert '"model":"qwen2.5:0.5b"' in output
    assert '"duration_ms":' in output
    assert '"timeout_seconds":20.0' in output
    assert "synthetic secret prompt" not in output
    assert "synthetic secret reply" not in output


def test_inference_timeout_is_classified_and_logged_without_content(monkeypatch, caplog):
    from fastapi import HTTPException
    import logging

    def timeout(*args, **kwargs):
        raise TimeoutError("synthetic timeout detail")

    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama", "qwen2.5:0.5b", 20.0))
    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", timeout)
    caplog.set_level(logging.INFO, logger=qwen_gateway.__name__)

    try:
        qwen_gateway.qwen_chat([{"role": "user", "content": "synthetic secret prompt"}])
    except HTTPException as exc:
        assert exc.status_code == 504
        assert exc.detail["code"] == "inference_timeout"
    else:
        raise AssertionError("Timeout must be returned as a structured inference failure")

    output = "\n".join(record.getMessage() for record in caplog.records)
    assert '"timed_out":true' in output
    assert '"failure_reason":"socket_timeout"' in output
    assert "synthetic secret prompt" not in output
    assert "synthetic timeout detail" not in output



def test_production_evaluation_accepts_only_the_active_main_branch_manual_workflow(monkeypatch):
    from starlette.requests import Request
    from app.api import collaboration

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({
                "repository": {"full_name": "getmycareos-star/SolenOS_backend"},
                "event": "workflow_dispatch",
                "path": ".github/workflows/production-qwen-evaluation.yml",
                "head_branch": "main",
                "status": "in_progress",
            }).encode("utf-8")

    monkeypatch.setattr(collaboration.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/assistant/evaluate-production",
        "headers": [
            (b"authorization", b"Bearer ephemeral-github-token"),
            (b"x-github-run-id", b"123456"),
        ],
    })
    collaboration._verify_production_evaluation_run(request)


def test_production_evaluation_rejects_non_workflow_bearer(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.api import collaboration

    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/assistant/evaluate-production",
        "headers": [(b"authorization", b"Bearer arbitrary-token"), (b"x-github-run-id", b"123")],
    })
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({
                "repository": {"full_name": "attacker/other-repository"},
                "event": "workflow_dispatch",
                "path": ".github/workflows/production-qwen-evaluation.yml",
                "head_branch": "main",
                "status": "in_progress",
            }).encode("utf-8")

    monkeypatch.setattr(collaboration.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    try:
        collaboration._verify_production_evaluation_run(request)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("Unauthorised production evaluation request must be rejected")


def test_readiness_endpoint_uses_cached_probe_instead_of_forcing_each_request(monkeypatch):
    from app.api import collaboration

    calls = []

    def readiness(force=False):
        calls.append(force)
        return {"available": False, "state": "unavailable", "failure_reason": "inference_timeout"}

    monkeypatch.setattr(collaboration, "qwen_readiness_status", readiness)
    result = collaboration.qwen_assistant_readiness()

    assert result["failure_reason"] == "inference_timeout"
    assert calls == [False]
