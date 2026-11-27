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

    def fake_qwen_chat(messages, *, options=None, audit_context=None):
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

    def fake_qwen_chat(messages, *, options=None, audit_context=None):
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

def test_qwen_context_budget_retries_at_96_tokens_when_128_still_exceeds_fixed_prompt(monkeypatch):
    context_lines = [
        "Canonical source: " + json.dumps({
            "source_type": "events",
            "record": {
                "title": "Saved caregiver observation",
                "description": "Mum refused lunch yesterday.",
                "information_status": "caregiver_reported",
            },
        })
    ]
    attempts = []

    def fake_qwen_chat(messages, *, options=None, audit_context=None):
        attempts.append({
            "prompt": messages[0]["content"],
            "num_predict": options["num_predict"],
        })
        if options["num_predict"] >= 128:
            raise HTTPException(status_code=413, detail="fixed instructions exceed input budget")
        return "The saved caregiver note reports that Mum refused lunch yesterday."

    monkeypatch.setattr(collaboration, "qwen_chat", fake_qwen_chat)
    reply, selected_raw, rendered, truncated = asyncio.run(
        collaboration._qwen_chat_with_bounded_context(
            "Shared SolenOS instructions. Authorized context:\\n",
            "What was recorded about lunch?",
            context_lines,
            options={"temperature": 0, "num_predict": 256},
        )
    )

    assert {attempt["num_predict"] for attempt in attempts} >= {256, 128, 96}
    assert attempts[-1]["num_predict"] == 96
    assert "Saved caregiver observation" in attempts[-1]["prompt"]
    assert reply == "The saved caregiver note reports that Mum refused lunch yesterday."
    assert selected_raw == context_lines
    assert rendered
    assert truncated is False


def test_qwen_context_budget_never_reduces_safety_response_reservation(monkeypatch):
    attempts = []

    def reject_oversized_prompt(messages, *, options=None, audit_context=None):
        attempts.append(options["num_predict"])
        raise HTTPException(status_code=413, detail="safety response reservation must be preserved")

    monkeypatch.setattr(collaboration, "qwen_chat", reject_oversized_prompt)
    try:
        asyncio.run(
            collaboration._qwen_chat_with_bounded_context(
                "Safety instructions. Authorized context:\\n",
                "I think Mum may be having trouble breathing.",
                ["Canonical source: " + json.dumps({"source_type": "events", "status": "available"})],
                options={"temperature": 0, "num_predict": 256},
                allow_output_budget_fallback=False,
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 413
    else:
        raise AssertionError("an oversized safety prompt must fail closed instead of shortening its response budget")

    assert attempts
    assert set(attempts) == {256}


def test_focused_first_visit_assist_prompt_fits_configured_qwen_context():
    from app.services import qwen_gateway
    from app.services.qwen_specialized_prompts import apply_specialized_intelligence
    from app.services.qwen_system_prompt import apply_solenos_system_contract

    coverage = {
        "schema_status": "complete",
        "history_coverage": "complete_history_scan_ranked_subset",
        "retrieval_scan_complete": True,
        "retrieval_projection_complete": True,
        "missing_required_sources": [],
        "missing_optional_sources": [],
        "sources": {
            name: {
                "status": "available" if name in {"events", "evidence"} else "empty",
                "candidate_scan_complete": True,
                "has_more": False,
                "selection_truncated": False,
            }
            for name in (
                "living_record", "events", "evidence", "evidence_observations",
                "evidence_freshness_assessments", "facts", "assertions", "corrections",
                "conflicts", "contradictions", "uncertainties", "uncertainty_assessments",
                "open_questions",
            )
        },
    }
    prefix = collaboration._build_companion_prompt_prefix(
        "living_care_record",
        "Summarize only authorized recorded context. Preserve provenance and uncertainty.",
        "",
        "",
        "Target 45 words; hard maximum 70 words.",
        ["Canonical LCR retrieval coverage: " + json.dumps(coverage)],
    )
    messages = [
        {"role": "system", "content": prefix + "No matching recorded context was found."},
        {"role": "user", "content": "Interpret this newly saved caregiver-reported event using only authorized care information."},
    ]
    prepared = apply_solenos_system_contract(apply_specialized_intelligence(messages))
    estimate = qwen_gateway._estimate_prompt_tokens(prepared)
    input_budget = 4096 - 128 - 128
    assert estimate <= input_budget, {
        "estimated_prompt_tokens": estimate,
        "input_budget": input_budget,
        "prompt_bytes": sum(
            len(str(message.get("content") or "").encode("utf-8"))
            for message in prepared
        ),
    }

def test_companion_retrieval_coverage_keeps_gaps_without_listing_all_available_sources():
    context = {
        "schema_status": "available",
        "history_coverage": "partial",
        "missing_required_sources": ["documents", "professional_contributions"],
        "retrieval_scan_complete": False,
        "sources": {
            **{f"available_{index}": {"status": "available"} for index in range(30)},
            "empty_optional": {"status": "empty"},
            "appointments": {
                "status": "partial",
                "interpretation_limit": "Some appointment rows could not be loaded.",
            },
            "documents": {
                "status": "unavailable_schema",
                "interpretation_limit": "Document schema is not available.",
            },
        },
    }

    summary = collaboration._compact_lcr_retrieval_coverage(context)

    assert summary["available_source_count"] == 30
    assert summary["empty_source_count"] == 1
    assert "available_0" not in summary["non_available_sources"]
    assert "empty_optional" not in summary["non_available_sources"]
    assert summary["non_available_sources"]["appointments"]["status"] == "partial"
    assert summary["non_available_sources"]["documents"]["status"] == "unavailable_schema"
    assert summary["missing_required_sources"] == ["documents", "professional_contributions"]
    assert summary["retrieval_scan_complete"] is False


def test_real_companion_first_visit_prompt_fits_4096_token_window_with_care_context():
    from app.services.qwen_gateway import _enforce_prompt_context_budget
    from app.services.qwen_specialized_prompts import apply_specialized_intelligence
    from app.services.qwen_system_prompt import apply_solenos_system_contract

    coverage = collaboration._compact_lcr_retrieval_coverage({
        "schema_status": "available",
        "history_coverage": "partial",
        "missing_required_sources": ["documents"],
        "retrieval_scan_complete": True,
        "sources": {
            **{f"available_{index}": {"status": "available"} for index in range(24)},
            "documents": {
                "status": "unavailable_schema",
                "interpretation_limit": "Document records were not available to this request.",
            },
        },
    })
    coverage_lines = [
        "Canonical LCR retrieval coverage: " + json.dumps(coverage),
        "Canonical LCR prompt coverage: " + json.dumps({
            "selected_records": 1,
            "selected_chars": 310,
            "selection_truncated": False,
        }),
    ]
    prefix = collaboration._build_companion_prompt_prefix(
        "companion",
        "Respond to the caregiver's immediate request using authorized context when provided. Be concise and practical.",
        "",
        "",
        "Target 70 words; hard maximum 120 words.",
        coverage_lines,
    )
    context_line = (
        "Canonical LCR source (events; information_status=caregiver_reported): "
        + json.dumps({
            "title": "Breakfast observation",
            "description": "Caregiver reported Mum forgot whether she had eaten breakfast.",
            "occurred_at": "2026-11-24T08:00:00Z",
            "recorded_at": "2026-11-24T08:05:00Z",
            "information_status": "caregiver_reported",
        })
    )
    routed = apply_specialized_intelligence([
        {"role": "system", "content": prefix + context_line},
        {"role": "user", "content": "What did I just record?"},
    ])
    assembled = apply_solenos_system_contract(routed)

    _enforce_prompt_context_budget(assembled, num_ctx=4096, num_predict=256)
    assert "unavailable_schema" in assembled[0]["content"]
    assert "Breakfast observation" in assembled[0]["content"]
    assert "Never silently create, update, accept, withdraw, delete, verify, or resolve an LCR item" in assembled[0]["content"]
