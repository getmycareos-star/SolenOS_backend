from __future__ import annotations

import json
import urllib.error

from app.services import qwen_gateway


def test_startup_retry_socket_timeout_is_capped_by_remaining_retry_budget(monkeypatch):
    clock = [0.0]
    timeouts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"message":{"content":"real response shape"}}'

    def fake_urlopen(request, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            # Simulate a quick connection refusal during model startup.
            clock[0] += 0.25
            raise urllib.error.URLError(ConnectionRefusedError("runtime still starting"))
        return Response()

    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama", "qwen3.5:4b", 10.0))
    monkeypatch.setenv("OLLAMA_STARTUP_RETRY_SECONDS", "0.5")
    monkeypatch.setattr(qwen_gateway.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(qwen_gateway.time, "sleep", lambda _seconds: clock.__setitem__(0, clock[0] + 0.1))
    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", fake_urlopen)

    result = qwen_gateway.qwen_chat([{"role": "user", "content": "synthetic startup retry test"}])

    assert result == "real response shape"
    assert len(timeouts) == 2
    assert timeouts[0] == 10.0
    # 0.1 seconds of retry backoff leaves 0.4 seconds of the 0.5-second
    # startup budget. The retry socket timeout must not retain the 10s default.
    assert 0 < timeouts[1] <= 0.4


def test_startup_retry_budget_zero_does_not_retry(monkeypatch):
    calls = []

    def refused(request, timeout):
        calls.append(timeout)
        raise urllib.error.URLError(ConnectionRefusedError("runtime still starting"))

    monkeypatch.setattr(qwen_gateway, "get_ollama_config", lambda: ("http://ollama", "qwen3.5:4b", 10.0))
    monkeypatch.setenv("OLLAMA_STARTUP_RETRY_SECONDS", "0")
    monkeypatch.setattr(qwen_gateway.urllib.request, "urlopen", refused)

    try:
        qwen_gateway.qwen_chat([{"role": "user", "content": "synthetic no-retry test"}])
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 503
    else:
        raise AssertionError("A refused connection must not be treated as successful inference")

    assert len(calls) == 1
