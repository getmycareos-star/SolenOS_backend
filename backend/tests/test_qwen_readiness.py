from __future__ import annotations

import json
import time

from app.services import qwen_gateway


def test_installation_state_is_machine_readable_and_restricts_failure_reason(tmp_path, monkeypatch):
    state_file = tmp_path / "qwen-state.json"
    monkeypatch.setenv("QWEN_INSTALL_STATE_FILE", str(state_file))
    state_file.write_text(json.dumps({
        "state": "failed",
        "model": "qwen3.5:4b",
        "updated_at": "2026-10-31T00:00:00+00:00",
        "failure_reason": "ollama_pull_failed",
    }), encoding="utf-8")

    status = qwen_gateway.qwen_installation_status()
    assert status == {
        "state": "failed",
        "model": "qwen3.5:4b",
        "updated_at": "2026-10-31T00:00:00+00:00",
        "failure_reason": "ollama_pull_failed",
    }


def test_missing_installation_state_defaults_to_not_started(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_INSTALL_STATE_FILE", str(tmp_path / "missing.json"))
    assert qwen_gateway.qwen_installation_status()["state"] == "not_started"


def test_readiness_polling_never_calls_runtime_or_inference(monkeypatch):
    monkeypatch.setattr(
        qwen_gateway,
        "get_ollama_config",
        lambda: (_ for _ in ()).throw(AssertionError("health polling must not resolve runtime config")),
    )
    monkeypatch.setattr(
        qwen_gateway,
        "qwen_chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("health polling must not run inference")),
    )
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("health polling must not call Ollama")),
    )
    qwen_gateway._READINESS_CACHE.update({"checked_at": 0.0, "result": None})

    result = qwen_gateway.qwen_readiness_status(force=True)

    assert result["available"] is False
    assert result["state"] == "not_tested"
    assert result["last_attempt_at"] is None
    assert result["source"] == "actual_inference"


def test_actual_inference_updates_readiness_without_logging_content(monkeypatch, caplog):
    import logging

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"message":{"content":"synthetic secret reply"}}'

    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama", "qwen3.5:4b", 20.0))
    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    qwen_gateway._READINESS_CACHE.update({"checked_at": 0.0, "result": None})
    caplog.set_level(logging.INFO, logger=qwen_gateway.__name__)

    answer = qwen_gateway.qwen_chat([{"role": "user", "content": "synthetic secret prompt"}])
    readiness = qwen_gateway.qwen_readiness_status()

    assert answer == "synthetic secret reply"
    assert readiness["available"] is True
    assert readiness["state"] == "ready"
    assert readiness["last_success_at"]
    output = "\n".join(record.getMessage() for record in caplog.records)
    assert '"event":"qwen_inference"' in output
    assert '"duration_ms":' in output
    assert "synthetic secret prompt" not in output
    assert "synthetic secret reply" not in output


def test_failed_inference_updates_readiness_and_preserves_last_success(monkeypatch):
    from fastapi import HTTPException

    prior_success = "2026-11-06T10:00:00+00:00"
    qwen_gateway._READINESS_CACHE.update({
        "checked_at": 0.0,
        "result": {"available": True, "state": "ready", "last_success_at": prior_success},
    })
    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama", "qwen3.5:4b", 20.0))
    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("hidden")))
    try:
        qwen_gateway.qwen_chat([{"role": "user", "content": "synthetic prompt"}])
    except HTTPException as exc:
        assert exc.status_code == 504
    else:
        raise AssertionError("inference timeout must fail")

    readiness = qwen_gateway.qwen_readiness_status()
    assert readiness["available"] is False
    assert readiness["state"] == "unavailable"
    assert readiness["last_success_at"] == prior_success
    assert readiness["failure_reason"] == "socket_timeout"


def test_stale_inference_is_not_reported_as_ready(monkeypatch):
    qwen_gateway._READINESS_CACHE.update({
        "checked_at": time.monotonic() - 301,
        "result": {"available": True, "state": "ready", "last_success_at": "2026-11-06T10:00:00+00:00"},
    })
    result = qwen_gateway.qwen_readiness_status()
    assert result["available"] is False
    assert result["state"] == "stale"
    assert result["stale"] is True


def test_runtime_inventory_separates_runtime_reachability_from_model_installation(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b'{"models":[{"name":"another-model"}]}'

    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama", "qwen3.5:4b", 20.0))
    monkeypatch.setattr(qwen_gateway, "qwen_installation_status", lambda: {"state": "downloading"})
    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", lambda *args, **kwargs: Response())

    result = qwen_gateway.qwen_runtime_status()

    assert result["runtime_available"] is True
    assert result["model_installed"] is False
    assert result["available"] is False
    assert result["installation"]["state"] == "downloading"


def test_assistant_status_does_not_claim_operational_when_only_inventory_is_available(monkeypatch):
    from app.api import collaboration

    monkeypatch.setattr(collaboration, "qwen_runtime_status", lambda: {
        "available": True,
        "runtime_available": True,
        "model_installed": True,
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
    assert status["runtime_available"] is True
    assert status["model_installed"] is True
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



def test_companion_does_not_run_a_redundant_readiness_inference_preflight():
    from app.api import collaboration

    # The actual qwen_chat call is authoritative. A second readiness inference
    # duplicated expensive model work and could reject slow-but-valid inference.
    assert not hasattr(collaboration, "_ensure_companion_available")



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

    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama", "qwen3.5:4b", 20.0))
    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    caplog.set_level(logging.INFO, logger=qwen_gateway.__name__)

    answer = qwen_gateway.qwen_chat([{"role": "user", "content": "synthetic secret prompt"}])
    assert answer == "synthetic secret reply"
    output = "\n".join(record.getMessage() for record in caplog.records)
    assert '"event":"qwen_inference"' in output
    assert '"model":"qwen3.5:4b"' in output
    assert '"duration_ms":' in output
    assert '"timeout_seconds":20.0' in output
    assert "synthetic secret prompt" not in output
    assert "synthetic secret reply" not in output


def test_inference_timeout_is_classified_and_logged_without_content(monkeypatch, caplog):
    from fastapi import HTTPException
    import logging

    def timeout(*args, **kwargs):
        raise TimeoutError("synthetic timeout detail")

    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama", "qwen3.5:4b", 20.0))
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


def test_readiness_endpoint_returns_last_inference_without_triggering_model_work(monkeypatch):
    from app.api import collaboration

    calls = []

    def readiness(force=False):
        calls.append(force)
        return {"available": False, "state": "not_tested", "source": "actual_inference"}

    monkeypatch.setattr(collaboration, "qwen_readiness_status", readiness)
    result = collaboration.qwen_assistant_readiness()

    assert result["state"] == "not_tested"
    assert result["source"] == "actual_inference"
    assert calls == [False]
