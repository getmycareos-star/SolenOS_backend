"""PostgreSQL integration for durable, privacy-minimizing Qwen inference audit records."""
import io
import json
import urllib.error

import pytest
from fastapi import HTTPException

from app.core.database import SessionLocal
from app.models.ai_output_governance import AIInferenceMetric
from app.services import qwen_gateway


pytestmark = pytest.mark.skipif(
    SessionLocal.kw["bind"].dialect.name != "postgresql",
    reason="Requires the migrated PostgreSQL integration database",
)


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


def test_qwen_gateway_persists_successful_invocation_metadata_without_care_content(monkeypatch):
    invocation_ids = []
    original_persist = qwen_gateway._persist_inference_metric

    def capture_persist(metric):
        invocation_ids.append(metric.id)
        return original_persist(metric)

    monkeypatch.setattr(qwen_gateway, "_persist_inference_metric", capture_persist)
    monkeypatch.setattr(
        qwen_gateway,
        "get_ollama_config",
        lambda: ("http://ollama.internal", "qwen-test", 2.0),
    )
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(json.dumps({
            "message": {"role": "assistant", "content": "The supplied record has no sleep entry."},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 42,
            "eval_count": 7,
            "total_duration": 123456,
            "load_duration": 12345,
        }).encode("utf-8")),
    )

    answer = qwen_gateway.qwen_chat([
        {"role": "system", "content": "SOLENOS_WORKFLOW_ID: companion. Use authorized evidence only."},
        {"role": "user", "content": "PRIVATE_SYNTHETIC_CARE_TEXT_7c21: Did Dad sleep well?"},
    ])

    assert answer == "The supplied record has no sleep entry."
    assert len(invocation_ids) == 1

    with SessionLocal() as db:
        metric = db.get(AIInferenceMetric, invocation_ids[0])
        assert metric is not None
        assert metric.outcome == "succeeded"
        assert metric.latency_ms is not None and metric.latency_ms >= 0
        assert metric.input_tokens == 42
        assert metric.output_tokens == 7
        assert metric.total_tokens == 49
        assert metric.person_id is None
        metadata = metric.metrics_json
        assert metadata["provider"] == "ollama"
        assert metadata["transport"] == "native_chat"
        assert metadata["workflow_id"] == "companion"
        assert metadata["operating_contract_version"] == qwen_gateway.SOLENOS_OPERATING_CONTRACT_VERSION
        assert len(metadata["system_prompt_sha256"]) == 64
        assert len(metadata["request_context_sha256"]) == 64
        assert metadata["done"] is True
        assert metadata["done_reason"] == "stop"
        serialized = json.dumps(metadata)
        assert "PRIVATE_SYNTHETIC_CARE_TEXT_7c21" not in serialized
        assert "The supplied record has no sleep entry" not in serialized


def test_qwen_gateway_does_not_deliver_answer_when_audit_record_cannot_persist(monkeypatch):
    monkeypatch.setattr(
        qwen_gateway,
        "get_ollama_config",
        lambda: ("http://ollama.internal", "qwen-test", 2.0),
    )
    monkeypatch.setattr(qwen_gateway, "_persist_inference_metric", lambda _metric: False)
    def unexpected_model_request(*args, **kwargs):
        raise AssertionError("model must not be called when the audit start receipt cannot persist")

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", unexpected_model_request)

    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([
            {"role": "system", "content": "SOLENOS_WORKFLOW_ID: companion."},
            {"role": "user", "content": "Use synthetic evidence."},
        ])

    assert getattr(error.value, "status_code", None) == 503
    assert error.value.detail["code"] == "inference_audit_unavailable"

def test_qwen_gateway_persists_failed_invocation_outcome_on_postgres(monkeypatch):
    invocation_ids = []
    original_persist = qwen_gateway._persist_inference_metric

    def capture_persist(metric):
        invocation_ids.append(metric.id)
        return original_persist(metric)

    monkeypatch.setattr(qwen_gateway, "_persist_inference_metric", capture_persist)
    monkeypatch.setattr(
        qwen_gateway,
        "get_ollama_config",
        lambda: ("http://ollama.internal", "qwen-test", 2.0),
    )

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
        qwen_gateway.qwen_chat([
            {"role": "system", "content": "SOLENOS_WORKFLOW_ID: companion."},
            {"role": "user", "content": "Synthetic request without personal care content."},
        ])

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "model_not_installed"
    assert len(invocation_ids) == 1
    with SessionLocal() as db:
        metric = db.get(AIInferenceMetric, invocation_ids[0])
        assert metric is not None
        assert metric.outcome == "failed"
        assert metric.metrics_json["invocation_state"] == "finished"
        assert metric.metrics_json["upstream_error_category"] == "model_not_installed"

