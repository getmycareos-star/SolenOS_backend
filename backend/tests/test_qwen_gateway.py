import io
import json
import urllib.error

import pytest
from fastapi import HTTPException

from app.services import qwen_gateway


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


def configure_local_model(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen-test")
    monkeypatch.setenv("OLLAMA_SIDECAR_ENABLED", "true")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "test")


def test_qwen_chat_classifies_missing_model_or_route_as_503(monkeypatch):
    configure_local_model(monkeypatch)

    def missing_model(*args, **kwargs):
        raise urllib.error.HTTPError(
            "http://127.0.0.1:11434/api/chat",
            404,
            "Not Found",
            None,
            io.BytesIO(b'{"error":"model not found"}'),
        )

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", missing_model)
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hello"}])
    assert error.value.status_code == 503
    assert "returned 404" in error.value.detail
    assert "verify that the model is installed" in error.value.detail


def test_qwen_chat_classifies_timeout_as_504(monkeypatch):
    configure_local_model(monkeypatch)

    def timeout(*args, **kwargs):
        raise TimeoutError("inference timed out")

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", timeout)
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hello"}])
    assert error.value.status_code == 504
    assert "timed out" in error.value.detail


def test_qwen_chat_rejects_malformed_json_response(monkeypatch):
    configure_local_model(monkeypatch)
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(b"{not-json"),
    )
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hello"}])
    assert error.value.status_code == 503
    assert "malformed JSON" in error.value.detail


def test_qwen_chat_rejects_invalid_message_shape_and_empty_content(monkeypatch):
    configure_local_model(monkeypatch)
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(json.dumps({"message": []}).encode()),
    )
    with pytest.raises(HTTPException) as invalid_shape:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hello"}])
    assert invalid_shape.value.status_code == 503
    assert "valid message object" in invalid_shape.value.detail

    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(json.dumps({"message": {"content": "  "}}).encode()),
    )
    with pytest.raises(HTTPException) as empty:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hello"}])
    assert empty.value.status_code == 503
    assert "no usable response" in empty.value.detail


def test_qwen_runtime_status_rejects_non_object_payload(monkeypatch):
    configure_local_model(monkeypatch)
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(b"[]"),
    )
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_runtime_status()
    assert error.value.status_code == 503
    assert "invalid runtime status object" in error.value.detail
