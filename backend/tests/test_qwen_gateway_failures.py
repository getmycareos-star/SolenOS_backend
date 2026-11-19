import io
import json
import urllib.error

import pytest
from fastapi import HTTPException

from app.services import qwen_gateway


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def configure_gateway(monkeypatch):
    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama.internal", "qwen-test", 2.0))


def test_qwen_gateway_classifies_missing_model_separately(monkeypatch):
    configure_gateway(monkeypatch)

    def missing_model(*args, **kwargs):
        raise urllib.error.HTTPError(
            "http://ollama.internal/api/chat",
            404,
            "Not Found",
            {},
            io.BytesIO(b'{"error":"model qwen-test not found"}'),
        )

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", missing_model)
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hi"}])
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "model_not_installed"
    assert error.value.detail["reason"] == "configured_model_missing"


def test_qwen_gateway_distinguishes_connection_failure(monkeypatch):
    configure_gateway(monkeypatch)
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.URLError("connection refused")),
    )
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hi"}])
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "runtime_unreachable"

def test_qwen_gateway_retries_connection_refused_during_model_cold_start(monkeypatch):
    configure_gateway(monkeypatch)
    monkeypatch.setenv("OLLAMA_STARTUP_RETRY_SECONDS", "5")
    monkeypatch.setattr(qwen_gateway.time, "sleep", lambda _seconds: None)
    attempts = {"count": 0}

    def start_after_first_attempt(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise urllib.error.URLError(ConnectionRefusedError(111, "connection refused"))
        return FakeResponse(json.dumps({"message": {"content": "Model is ready."}}).encode())

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", start_after_first_attempt)
    assert qwen_gateway.qwen_chat([{"role": "user", "content": "hi"}]) == "Model is ready."
    assert attempts["count"] == 2


def test_qwen_gateway_retries_remote_disconnect_during_model_cold_start(monkeypatch):
    from http.client import RemoteDisconnected

    configure_gateway(monkeypatch)
    monkeypatch.setenv("OLLAMA_STARTUP_RETRY_SECONDS", "5")
    monkeypatch.setattr(qwen_gateway.time, "sleep", lambda _seconds: None)
    attempts = {"count": 0}

    def start_after_first_attempt(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise urllib.error.URLError(RemoteDisconnected("connection closed during model startup"))
        return FakeResponse(json.dumps({"message": {"content": "Model is ready."}}).encode())

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", start_after_first_attempt)
    assert qwen_gateway.qwen_chat([{"role": "user", "content": "hi"}]) == "Model is ready."
    assert attempts["count"] == 2


def test_qwen_gateway_distinguishes_timeout(monkeypatch):
    configure_gateway(monkeypatch)
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("timed out")),
    )
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hi"}])
    assert error.value.status_code == 504
    assert error.value.detail["code"] == "inference_timeout"
    assert error.value.detail["reason"] == "socket_timeout"


def test_qwen_gateway_rejects_malformed_response_json(monkeypatch):
    configure_gateway(monkeypatch)
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(b"{not-json"),
    )
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hi"}])
    assert error.value.status_code == 502
    assert error.value.detail["code"] == "invalid_upstream_response"


def test_qwen_gateway_rejects_empty_model_output(monkeypatch):
    configure_gateway(monkeypatch)
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(json.dumps({"message": {"content": "  "}}).encode()),
    )
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hi"}])
    assert error.value.status_code == 502
    assert error.value.detail["code"] == "empty_model_response"


def test_qwen35_gateway_disables_thinking_and_returns_user_visible_content(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        qwen_gateway,
        "get_ollama_config",
        lambda: ("http://ollama.internal", "qwen3.5:4b", 2.0),
    )

    def capture_request(request, **kwargs):
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse(json.dumps({"message": {"content": "The synthetic record is unverified."}}).encode())

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", capture_request)
    answer = qwen_gateway.qwen_chat([{"role": "user", "content": "Summarize the synthetic record."}])

    assert answer == "The synthetic record is unverified."
    assert captured["model"] == "qwen3.5:4b"
    assert captured["think"] is False


def test_qwen35_gateway_rejects_thinking_only_response(monkeypatch):
    monkeypatch.setattr(
        qwen_gateway,
        "get_ollama_config",
        lambda: ("http://ollama.internal", "qwen3.5:4b", 2.0),
    )
    response = {
        "message": {
            "role": "assistant",
            "thinking": "private reasoning only",
            "content": "",
        }
    }
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(json.dumps(response).encode()),
    )

    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "Confirm receipt."}])

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "empty_model_response"
    assert error.value.detail["reason"] == "missing_message_content"


def test_production_qwen_gateway_rejects_public_inference_egress(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://public-model.example.com:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen-test")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.delenv("OLLAMA_SIDECAR_ENABLED", raising=False)

    with pytest.raises(HTTPException) as error:
        qwen_gateway.get_ollama_config()

    assert error.value.status_code == 503
    assert "private Railway" in error.value.detail


def test_production_qwen_gateway_accepts_only_private_railway_runtime(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://solenos-ollama.railway.internal:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen-test")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")

    base, model, _timeout = qwen_gateway.get_ollama_config()

    assert base == "http://solenos-ollama.railway.internal:11434"
    assert model == "qwen-test"

def test_qwen_gateway_defaults_to_production_safe_context_and_allows_explicit_upgrade(monkeypatch):
    captured = []
    configure_gateway(monkeypatch)
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)

    def capture_request(request, **kwargs):
        captured.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse(json.dumps({"message": {"content": "Grounded response."}}).encode())

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", capture_request)
    qwen_gateway.qwen_chat([{"role": "user", "content": "Use the authorized care history."}])
    assert captured[-1]["options"]["num_ctx"] == 4096

    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    qwen_gateway.qwen_chat([{"role": "user", "content": "Use the authorized care history."}])
    assert captured[-1]["options"]["num_ctx"] == 8192



def test_qwen_gateway_cannot_bypass_runtime_resource_bounds_with_call_options(monkeypatch):
    configure_gateway(monkeypatch)
    captured = {}

    def capture_request(request, **kwargs):
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse(json.dumps({"message": {"content": "Bounded response."}}).encode())

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", capture_request)
    answer = qwen_gateway.qwen_chat(
        [{"role": "user", "content": "Use the authorized care history."}],
        options={
            "num_ctx": 65536,
            "num_predict": 100000,
            "num_thread": 64,
            "temperature": 9,
            "top_p": 4,
            "top_k": 1000,
            "repeat_penalty": 9,
            "num_gpu": 999,
            "use_mmap": False,
        },
    )

    assert answer == "Bounded response."
    assert captured["options"] == {
        "num_ctx": 8192,
        "num_predict": 1024,
        "num_thread": 8,
        "temperature": 2.0,
        "top_p": 1.0,
        "top_k": 100,
        "repeat_penalty": 2.0,
    }


def test_qwen_gateway_ignores_invalid_quality_overrides(monkeypatch):
    configure_gateway(monkeypatch)
    captured = {}

    def capture_request(request, **kwargs):
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse(json.dumps({"message": {"content": "Safe defaults."}}).encode())

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", capture_request)
    qwen_gateway.qwen_chat(
        [{"role": "user", "content": "Continue."}],
        options={"temperature": "not-a-number", "num_ctx": "invalid"},
    )

    assert captured["options"]["num_ctx"] == 4096
    assert captured["options"]["num_predict"] == 96
    assert captured["options"]["num_thread"] == 2
    assert captured["options"]["temperature"] == 0.2
