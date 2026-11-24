import io
import http.client
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
    attempts = {"count": 0}

    def missing_model(*args, **kwargs):
        attempts["count"] += 1
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
    assert attempts["count"] == 1, "a missing model must not be retried"


def test_qwen_gateway_retries_one_transient_upstream_http_failure(monkeypatch):
    configure_gateway(monkeypatch)
    monkeypatch.setattr(qwen_gateway.time, "sleep", lambda _seconds: None)
    attempts = {"count": 0}

    def transient_then_success(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise urllib.error.HTTPError(
                "http://ollama.internal/api/chat",
                503,
                "Service Unavailable",
                {"Retry-After": "0"},
                io.BytesIO(b'{"error":"model is loading"}'),
            )
        return FakeResponse(json.dumps({"message": {"content": "Recovered response."}}).encode())

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", transient_then_success)
    assert qwen_gateway.qwen_chat([{"role": "user", "content": "hi"}]) == "Recovered response."
    assert attempts["count"] == 2


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


@pytest.mark.parametrize(
    "connection_error",
    [
        ConnectionRefusedError,
        ConnectionResetError,
        ConnectionAbortedError,
        BrokenPipeError,
        http.client.RemoteDisconnected,
    ],
)
def test_qwen_gateway_retries_direct_connection_failures_during_model_cold_start(
    monkeypatch, connection_error
):
    configure_gateway(monkeypatch)
    monkeypatch.setenv("OLLAMA_STARTUP_RETRY_SECONDS", "5")
    monkeypatch.setattr(qwen_gateway.time, "sleep", lambda _seconds: None)
    attempts = {"count": 0}

    def start_after_first_attempt(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise connection_error("connection failed before response headers")
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


def test_qwen_gateway_classifies_truncated_http_response(monkeypatch):
    import http.client

    configure_gateway(monkeypatch)

    class TruncatedResponse(FakeResponse):
        def read(self):
            raise http.client.IncompleteRead(b'{"message":')

    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: TruncatedResponse(b""),
    )

    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hi"}])

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "invalid_upstream_response"
    assert error.value.detail["reason"] == "IncompleteRead"


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


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b'{"message": null}',
        b'{"message": "not-an-object"}',
        b'{"unexpected": "response-shape"}',
    ],
)
def test_qwen_gateway_rejects_valid_json_with_unexpected_response_shape(monkeypatch, payload):
    configure_gateway(monkeypatch)
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hi"}])

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "invalid_upstream_response"
    assert error.value.detail["reason"] == "unexpected_response_shape"


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


def test_qwen_gateway_rejects_non_qwen_ollama_models(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://solenos-ollama.railway.internal:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")

    with pytest.raises(HTTPException) as error:
        qwen_gateway.get_ollama_config()

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "unsupported_model"
    assert error.value.detail["reason"] == "qwen_model_required"


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


@pytest.mark.parametrize("payload", [b"[]", b"null", b'"not-an-object"'])
def test_qwen_readiness_and_runtime_status_reject_non_object_inventory(monkeypatch, payload):
    configure_gateway(monkeypatch)
    monkeypatch.setenv("QWEN_INSTALL_STATE_FILE", "/tmp/solenos-test-missing-qwen-state.json")
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    readiness = qwen_gateway.qwen_readiness_status(force=True)
    runtime = qwen_gateway.qwen_runtime_status()

    assert readiness["available"] is False
    assert readiness["failure_reason"] == "invalid_model_inventory"
    assert readiness["inference_verified"] is False
    assert runtime["available"] is False
    assert runtime["error_category"] == "invalid_model_inventory"
    assert runtime["inference_verified"] is False


def test_qwen_readiness_and_runtime_status_reject_truncated_inventory(monkeypatch):
    import http.client

    configure_gateway(monkeypatch)
    monkeypatch.setenv("QWEN_INSTALL_STATE_FILE", "/tmp/solenos-test-missing-qwen-state.json")

    class TruncatedInventory(FakeResponse):
        def read(self):
            raise http.client.IncompleteRead(b'{"models":')

    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: TruncatedInventory(b""),
    )

    readiness = qwen_gateway.qwen_readiness_status(force=True)
    runtime = qwen_gateway.qwen_runtime_status()

    assert readiness["available"] is False
    assert readiness["error_category"] == "invalid_model_inventory"
    assert readiness["failure_reason"] == "IncompleteRead"
    assert runtime["available"] is False
    assert runtime["error_category"] == "invalid_model_inventory"
    assert runtime["failure_reason"] == "IncompleteRead"


def test_qwen_readiness_and_runtime_status_reject_inventory_without_models(monkeypatch):
    configure_gateway(monkeypatch)
    monkeypatch.setenv("QWEN_INSTALL_STATE_FILE", "/tmp/solenos-test-missing-qwen-state.json")
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(b'{"unexpected": []}'),
    )

    readiness = qwen_gateway.qwen_readiness_status(force=True)
    runtime = qwen_gateway.qwen_runtime_status()

    assert readiness["failure_reason"] == "invalid_model_inventory"
    assert runtime["error_category"] == "invalid_model_inventory"

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
        "num_ctx": 4096,
        "num_predict": 1024,
        "num_thread": 2,
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


def test_actual_qwen_transport_receives_global_and_workflow_specialist_contract(monkeypatch):
    configure_gateway(monkeypatch)
    captured = {}

    def capture_request(request, **kwargs):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(json.dumps({"message": {"content": "Grounded response."}}).encode())

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", capture_request)
    answer = qwen_gateway.qwen_chat([
        {"role": "system", "content": "SOLENOS_WORKFLOW_ID: medication_day. Report only supplied medication facts."},
        {"role": "user", "content": "The supplied list is historical."},
    ])

    assert answer == "Grounded response."
    system = captured["payload"]["messages"][0]["content"]
    assert "GLOBAL AI OPERATING CONTRACT" in system
    assert "SPECIALIST: MEDICATION AND TREATMENT CONTINUITY" in system
    assert "SPECIALIST: LCR RETRIEVAL AND CONTEXT INTELLIGENCE" in system
    assert "SPECIALIST: CAREGIVER COMPANION" not in system


def test_actual_qwen_transport_applies_the_exact_route_for_every_enabled_workflow(monkeypatch):
    from app.services.qwen_specialized_prompts import (
        SPECIALIZED_PROMPTS,
        WORKFLOWS_WITHOUT_SPECIALISTS,
        WORKFLOW_SPECIALISTS,
    )
    from app.services.qwen_workflows import ENABLED_WORKFLOWS

    configure_gateway(monkeypatch)
    captured = {}

    def capture_request(request, **kwargs):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(json.dumps({"message": {"content": "Grounded response."}}).encode())

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", capture_request)

    # Include internal production workflows such as companion care updates
    # and document candidate extraction, not only public assist enum values.
    all_routed_workflows = sorted(set(ENABLED_WORKFLOWS) | set(WORKFLOW_SPECIALISTS))
    output_contract = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "review_required": {"type": "boolean"}},
        "required": ["answer", "review_required"],
        "additionalProperties": False,
    }
    for workflow in all_routed_workflows:
        captured.clear()
        answer = qwen_gateway.qwen_chat(
            [
                {"role": "system", "content": f"SOLENOS_WORKFLOW_ID: {workflow}."},
                {"role": "user", "content": "Use only the supplied synthetic evidence."},
            ],
            response_format=output_contract,
        )
        assert answer == "Grounded response.", workflow
        payload = captured["payload"]
        assert payload["format"] == output_contract, workflow
        assert payload["messages"], workflow
        system = payload["messages"][0]["content"]
        assert "GLOBAL AI OPERATING CONTRACT" in system, workflow
        assert "SOLENOS COMPANION — THOUGHTFUL CARE PARTNER" in system, workflow
        assert "SOLENOS COMPANION — PRODUCT IDENTITY" in system, workflow

        expected = WORKFLOW_SPECIALISTS.get(workflow, ())
        assert workflow in WORKFLOWS_WITHOUT_SPECIALISTS or expected, workflow
        for specialist, prompt in SPECIALIZED_PROMPTS.items():
            assert (prompt in system) == (specialist in expected), {
                "workflow": workflow,
                "specialist": specialist,
                "expected": expected,
            }


def test_actual_qwen_transport_rejects_unmapped_workflow_before_network_call(monkeypatch):
    configure_gateway(monkeypatch)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("unmapped workflow must not reach Ollama")

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", unexpected_request)
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([
            {"role": "system", "content": "SOLENOS_WORKFLOW_ID: future_workflow"},
            {"role": "user", "content": "Hello."},
        ])

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "qwen_workflow_routing_unavailable"


def test_qwen_gateway_rejects_oversized_prompt_before_network_call(monkeypatch):
    configure_gateway(monkeypatch)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("oversized prompt must be rejected before Ollama")

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", unexpected_request)
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([
            {"role": "user", "content": "care evidence " * 5000}
        ])

    assert error.value.status_code == 413
    assert error.value.detail["code"] == "qwen_prompt_context_budget_exceeded"
    assert error.value.detail["estimated_prompt_tokens"] > error.value.detail["input_token_budget"]


def test_qwen_prompt_token_estimator_counts_utf8_and_message_framing():
    messages = [
        {"role": "system", "content": "SolenOS care contract."},
        {"role": "user", "content": "Mum ate breakfast. Café notes."},
    ]
    estimate = qwen_gateway._estimate_prompt_tokens(messages)
    assert estimate >= (len("SolenOS care contract.Mum ate breakfast. Café notes.".encode("utf-8")) // 4)
    assert estimate > 16


def test_qwen_gateway_sends_product_identity_in_actual_inference_payload(monkeypatch):
    configure_gateway(monkeypatch)
    captured = {}

    def capture_request(request, **kwargs):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(json.dumps({"message": {"content": "I'm SolenOS Companion."}}).encode())

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", capture_request)
    answer = qwen_gateway.qwen_chat([
        {"role": "user", "content": "Who are you?"}
    ])

    assert answer == "I'm SolenOS Companion."
    system = captured["payload"]["messages"][0]["content"]
    assert "SOLENOS COMPANION — PRODUCT IDENTITY" in system
    assert "SolenOS was founded by David Abuchi" in system
    assert "The model is replaceable." in system
    assert "SOLENOS COMPANION — THOUGHTFUL CARE PARTNER" in system
    assert "## REASSURING THROUGH CLARITY" in system
