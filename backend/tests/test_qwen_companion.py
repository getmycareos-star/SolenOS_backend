import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from app.services import conversation_service


def test_emergency_override_does_not_call_qwen():
    with patch.object(conversation_service, "_call_qwen") as provider:
        result = conversation_service.build_companion_reply(None, "person-1", "She is not breathing")
    assert result.guard_verdict == "blocked"
    assert result.provider_status == "safety_override"
    assert result.ai_generated is False
    provider.assert_not_called()


def test_qwen_unconfigured_is_explicit_and_not_generative():
    settings = SimpleNamespace(
        qwen_configured=False,
        qwen_base_url="",
        qwen_model="",
        qwen_api_key="",
        qwen_timeout_seconds=20,
    )
    with patch.object(conversation_service, "get_settings", return_value=settings):
        result = conversation_service._call_qwen("What changed?", "No records", [], [])
    assert result.provider_status == "not_configured"
    assert result.ai_generated is False
    assert "not configured" in result.reply.lower()


def test_qwen_citations_are_restricted_to_authorized_evidence_ids():
    settings = SimpleNamespace(
        qwen_configured=True,
        qwen_base_url="https://qwen.example/v1",
        qwen_model="qwen-test",
        qwen_api_key="test-key",
        qwen_timeout_seconds=3,
    )
    model_content = json.dumps({
        "reply": "The record notes a sleep change.",
        "evidence_ids": ["real-evidence", "invented-evidence"],
        "safety_note": None,
    })
    provider_body = json.dumps({
        "choices": [{"message": {"content": model_content}}]
    }).encode("utf-8")
    fake_response = io.BytesIO(provider_body)
    with patch.object(conversation_service, "get_settings", return_value=settings), patch.object(
        conversation_service, "urlopen", return_value=fake_response
    ):
        result = conversation_service._call_qwen(
            "What changed?", "EVIDENCE real-evidence: sleep notes", ["real-evidence"], []
        )
    assert result.ai_generated is True
    assert result.provider_status == "connected"
    assert result.evidence_ids == ["real-evidence"]
    assert "invented-evidence" not in result.evidence_ids


def test_qwen_provider_failure_does_not_fabricate_substitute_answer():
    settings = SimpleNamespace(
        qwen_configured=True,
        qwen_base_url="https://qwen.example/v1",
        qwen_model="qwen-test",
        qwen_api_key="test-key",
        qwen_timeout_seconds=3,
    )
    with patch.object(conversation_service, "get_settings", return_value=settings), patch.object(
        conversation_service, "urlopen", side_effect=OSError("network unavailable")
    ):
        result = conversation_service._call_qwen("What changed?", "No records", [], [])
    assert result.provider_status == "error"
    assert result.ai_generated is False
    assert "couldn't obtain a verified response" in result.reply.lower()
