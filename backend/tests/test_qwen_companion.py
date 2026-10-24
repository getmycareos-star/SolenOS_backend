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
        ollama_configured=False,
        ollama_base_url="",
        ollama_model="",
        ollama_timeout_seconds=20,
    )
    with patch.object(conversation_service, "get_settings", return_value=settings):
        result = conversation_service._call_qwen("What changed?", "No records", [], [])
    assert result.provider_status == "not_configured"
    assert result.ai_generated is False
    assert "not configured" in result.reply.lower()


def test_qwen_citations_are_restricted_to_authorized_evidence_ids():
    settings = SimpleNamespace(
        ollama_configured=True,
        ollama_base_url="http://ollama.railway.internal:11434",
        ollama_model="qwen-test",
        ollama_timeout_seconds=3,
    )
    model_result = {
        "reply": "The record notes a sleep change.",
        "evidence_ids": ["real-evidence", "invented-evidence"],
        "safety_note": None,
    }
    with patch.object(conversation_service, "get_settings", return_value=settings), patch.object(
        conversation_service, "call_qwen_json", return_value=(model_result, "connected")
    ) as qwen_call:
        result = conversation_service._call_qwen(
            "What changed?", "EVIDENCE real-evidence: sleep notes", ["real-evidence"], []
        )
    qwen_call.assert_called_once()
    assert "real-evidence" in qwen_call.call_args.kwargs["user_prompt"]
    system_prompt = qwen_call.call_args.kwargs["system_prompt"]
    assert "what the caregiver just said" in system_prompt
    assert "what the authorized Living Care Record actually records" in system_prompt
    assert "what remains unknown" in system_prompt
    assert result.ai_generated is True
    assert result.provider_status == "connected"
    assert result.evidence_ids == ["real-evidence"]
    assert "invented-evidence" not in result.evidence_ids


def test_qwen_provider_failure_does_not_fabricate_substitute_answer():
    settings = SimpleNamespace(
        ollama_configured=True,
        ollama_base_url="http://ollama.railway.internal:11434",
        ollama_model="qwen-test",
        ollama_timeout_seconds=3,
    )
    with patch.object(conversation_service, "get_settings", return_value=settings), patch.object(
        conversation_service, "call_qwen_json", return_value=(None, "error")
    ):
        result = conversation_service._call_qwen("What changed?", "No records", [], [])
    assert result.provider_status == "error"
    assert result.ai_generated is False
    assert "couldn't obtain a verified response" in result.reply.lower()
