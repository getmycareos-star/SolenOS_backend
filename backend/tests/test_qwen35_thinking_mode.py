import json
from io import BytesIO

from app.services import qwen_gateway


class _Response:
    def __init__(self, payload):
        self._body = BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._body.read()


def test_qwen35_chat_disables_thinking_and_returns_visible_content(monkeypatch):
    monkeypatch.setattr(
        qwen_gateway,
        "get_ollama_config",
        lambda: ("http://ollama.internal", "qwen3.5:4b", 30.0),
    )
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response({
            "message": {
                "role": "assistant",
                "thinking": "private reasoning",
                "content": "The request was received.",
            }
        })

    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", fake_urlopen)
    answer = qwen_gateway.qwen_chat([{"role": "user", "content": "Confirm receipt."}])

    assert captured["payload"]["model"] == "qwen3.5:4b"
    assert captured["payload"]["think"] is False
    assert answer == "The request was received."
    assert "private reasoning" not in answer
