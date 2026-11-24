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
                "source_text": "The caregiver's original note says breakfast was left unfinished. " * 6,
                "information_status": "caregiver_reported",
            },
        }),
    ]
    attempts = []

    def fake_qwen_chat(messages, *, options=None):
        prompt = messages[0]["content"]
        attempts.append(prompt)
        if len(prompt) > 600:
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
    assert len(attempts[-1]) <= 600
    assert "Some authorized records were omitted" in attempts[-1]
    assert reply == "The saved note reports an unfinished breakfast."
    assert selected_raw
    assert rendered
    assert truncated is True
    assert any("breakfast" in line.casefold() for line in rendered)
    assert len(selected_raw) < len(context_lines)

def test_daily_capture_extraction_prompt_fits_the_real_qwen_input_budget(monkeypatch):
    from app.services import qwen_gateway
    from app.services.qwen_specialized_prompts import apply_specialized_intelligence
    from app.services.qwen_system_prompt import apply_solenos_system_contract

    captured = {}

    def capture_messages(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return json.dumps({"observations": [], "open_questions": []})

    monkeypatch.setattr(qwen_gateway, "qwen_chat", capture_messages)
    qwen_gateway.extract_review_candidates(
        "Mum has been forgetting whether she has eaten breakfast.",
        source_event_id="event-first-visit",
        workflow_id="daily_capture",
    )

    prepared = apply_solenos_system_contract(
        apply_specialized_intelligence(captured["messages"])
    )
    estimate = qwen_gateway._estimate_prompt_tokens(prepared)
    input_budget = 4096 - int(captured["kwargs"]["options"]["num_predict"]) - 128
    assert estimate < input_budget, {
        "estimated_prompt_tokens": estimate,
        "input_budget": input_budget,
        "prompt_bytes": sum(
            len(str(message.get("content") or "").encode("utf-8"))
            for message in prepared
        ),
    }

def test_qwen_context_budget_reduces_output_reservation_before_discarding_context(monkeypatch):
    context_lines = [
        "Canonical source: " + json.dumps({
            "source_type": "events",
            "title": "First saved caregiver observation",
            "description": "Mum has been forgetting whether she has eaten breakfast.",
            "information_status": "caregiver_reported",
        })
    ]
    attempts = []

    def fake_qwen_chat(messages, *, options=None):
        attempts.append({
            "prompt": messages[0]["content"],
            "num_predict": options["num_predict"],
        })
        if options["num_predict"] > 128:
            raise HTTPException(status_code=413, detail="fixed instructions exceed input budget")
        return "The saved note records a concern about remembering breakfast."

    monkeypatch.setattr(collaboration, "qwen_chat", fake_qwen_chat)
    reply, selected_raw, rendered, truncated = asyncio.run(
        collaboration._qwen_chat_with_bounded_context(
            "Shared SolenOS instructions. Authorized context:\\n",
            "What does this saved update mean?",
            context_lines,
            options={"temperature": 0, "num_predict": 256},
        )
    )

    assert len(attempts) > 1
    assert attempts[0]["num_predict"] == 256
    assert attempts[-1]["num_predict"] == 128
    assert "First saved caregiver observation" in attempts[-1]["prompt"]
    assert reply == "The saved note records a concern about remembering breakfast."
    assert selected_raw == context_lines
    assert rendered
    assert truncated is False
