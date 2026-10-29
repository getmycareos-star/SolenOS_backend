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
    assert "model is not installed" in error.value.detail


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
    assert "unavailable" in error.value.detail


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
    assert "timed out" in error.value.detail


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
    assert "invalid response" in error.value.detail


def test_qwen_gateway_rejects_empty_model_output(monkeypatch):
    configure_gateway(monkeypatch)
    monkeypatch.setattr(
        qwen_gateway.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(json.dumps({"message": {"content": "  "}}).encode()),
    )
    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "hi"}])
    assert error.value.status_code == 503
    assert "no usable response" in error.value.detail
