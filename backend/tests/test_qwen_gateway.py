import json
from io import BytesIO

import pytest
from fastapi import HTTPException

from app.services import qwen_gateway


class _Response:
    def __init__(self, payload: dict):
        self._body = BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body.read()


def test_qwen35_disables_thinking_and_returns_only_user_facing_content(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:4b")
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response({
            "message": {
                "role": "assistant",
                "thinking": "private reasoning must not be surfaced",
                "content": "The request was received.",
            }
        })

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", fake_urlopen)

    result = qwen_gateway.qwen_chat([{"role": "user", "content": "Confirm receipt."}])

    assert captured["payload"]["think"] is False
    assert captured["payload"]["model"] == "qwen3.5:4b"
    assert result == "The request was received."
    assert "private reasoning" not in result


def test_qwen35_fails_closed_when_only_thinking_is_returned(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:4b")

    def fake_urlopen(_request, timeout):
        return _Response({
            "message": {
                "role": "assistant",
                "thinking": "reasoning only",
                "content": "",
            }
        })

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(HTTPException) as error:
        qwen_gateway.qwen_chat([{"role": "user", "content": "Confirm receipt."}])

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "empty_model_response"
    assert error.value.detail["reason"] == "missing_message_content"
