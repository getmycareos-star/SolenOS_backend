import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from app.services import qwen_gateway


def test_shared_gateway_calls_configured_ollama_native_chat_endpoint():
    settings = SimpleNamespace(
        ollama_configured=True,
        ollama_base_url="http://ollama.railway.internal:11434",
        ollama_model="qwen2.5:1.5b",
        ollama_timeout_seconds=15,
    )
    provider_body = json.dumps({
        "message": {"content": json.dumps({"summary": "Grounded summary", "post_ids": ["post-1"]})}
    }).encode()
    with patch.object(qwen_gateway, "get_settings", return_value=settings), patch.object(
        qwen_gateway, "urlopen", return_value=io.BytesIO(provider_body)
    ) as call:
        parsed, status = qwen_gateway.call_qwen_json(
            system_prompt="Return JSON.",
            user_prompt="Summarize only supplied evidence.",
        )
    request = call.call_args.args[0]
    assert request.full_url == "http://ollama.railway.internal:11434/api/chat"
    payload = json.loads(request.data)
    assert payload["model"] == "qwen2.5:1.5b"
    assert payload["format"] == "json"
    assert parsed == {"summary": "Grounded summary", "post_ids": ["post-1"]}
    assert status == "connected"


def test_shared_gateway_does_not_call_loopback_when_provider_is_unconfigured():
    settings = SimpleNamespace(
        ollama_configured=False,
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="qwen2.5:1.5b",
        ollama_timeout_seconds=15,
    )
    with patch.object(qwen_gateway, "get_settings", return_value=settings), patch.object(
        qwen_gateway, "urlopen"
    ) as call:
        parsed, status = qwen_gateway.call_qwen_json(
            system_prompt="Return JSON.", user_prompt="Test."
        )
    call.assert_not_called()
    assert parsed is None
    assert status == "not_configured"


def test_shared_gateway_fails_closed_on_transport_or_json_error():
    settings = SimpleNamespace(
        ollama_configured=True,
        ollama_base_url="http://ollama.railway.internal:11434",
        ollama_model="qwen2.5:1.5b",
        ollama_timeout_seconds=15,
    )
    with patch.object(qwen_gateway, "get_settings", return_value=settings), patch.object(
        qwen_gateway, "urlopen", side_effect=OSError("unavailable")
    ):
        parsed, status = qwen_gateway.call_qwen_json(
            system_prompt="Return JSON.", user_prompt="Test."
        )
    assert parsed is None
    assert status == "error"
