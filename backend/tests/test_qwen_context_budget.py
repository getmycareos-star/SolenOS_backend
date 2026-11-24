"""Regression tests for context-budget recovery before Qwen inference."""

import asyncio
import json

from fastapi import HTTPException

from app.api import collaboration


def test_qwen_context_budget_retries_with_balanced_source_context(monkeypatch):
    context_lines = [
        "Canonical source: " + json.dumps({
            "source_type": "events",
            "record": {
                "title": "Recent breakfast observation",
                "description": "Caregiver reported that breakfast was left unfinished.",
                "occurred_at": "2026-11-23T08:00:00Z",
                "reported_by_label": "Caregiver",
            },
        }),
        "Canonical source: " + json.dumps({
            "source_type": "corrections",
            "record": {
                "status": "reviewed",
                "description": "The earlier breakfast entry was corrected.",
                "recorded_at": "2026-11-23T09:00:00Z",
            },
        }),
        "Canonical source: " + json.dumps({
            "source_type": "evidence",
            "record": {
                "source_text": "The caregiver's original note says breakfast was left unfinished.",
                "information_status": "caregiver_reported",
            },
        }),
    ]
    attempts = []

    def fake_qwen_chat(messages, *, options=None):
        prompt = messages[0]["content"]
        attempts.append(prompt)
        if len(prompt) > 350:
            raise HTTPException(status_code=413, detail="context budget exceeded")
        return "The saved note reports an unfinished breakfast."

    monkeypatch.setattr(collaboration, "qwen_chat", fake_qwen_chat)
    reply, selected_raw, rendered, truncated = asyncio.run(
        collaboration._qwen_chat_with_bounded_context(
            "Shared instructions. Authorized context:\n",
            "What does this saved update mean?",
            context_lines,
            options={"temperature": 0, "num_predict": 256},
        )
    )

    assert len(attempts) > 1
    assert len(attempts[-1]) <= 350
    assert reply == "The saved note reports an unfinished breakfast."
    assert selected_raw
    assert rendered
    assert truncated is True
    assert any("breakfast" in line.casefold() for line in rendered)
    assert len(selected_raw) < len(context_lines)
