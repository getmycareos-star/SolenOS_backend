import json

from app.api.collaboration import (
    _care_history_extractive_answer,
    _care_history_reply_covers_requested_topics,
)


def _event_line(event_id: str, description: str) -> str:
    return "Shared LCR context: " + json.dumps({
        "source_type": "events",
        "record": {
            "id": event_id,
            "description": description,
            "status": "recorded",
            "created_by_caregiver_id": "caregiver-1",
            "occurred_at": "2026-11-14T18:00:00+00:00",
        },
    })


def test_history_fallback_covers_each_requested_topic_from_saved_event_reports():
    context = [
        _event_line("sleep-event", "Mum slept poorly last night and seemed more unsettled this morning."),
        _event_line("lunch-event", "Mum barely touched her lunch today. She drank some water, though."),
        _event_line("medication-event", "I think the doctor changed Mum's medication yesterday, but I can't remember the new dose."),
        _event_line("dinner-event", "Mum was calmer this evening. She ate a little more at dinner and is now resting."),
    ]

    answer = _care_history_extractive_answer(
        "What has the caregiver recorded about sleep, lunch and dinner?",
        context,
    )

    assert answer is not None
    assert "Mum slept poorly last night" in answer
    assert "Mum barely touched her lunch today" in answer
    assert "Mum was calmer this evening" in answer
    assert "changed Mum's medication" not in answer
    assert _care_history_reply_covers_requested_topics(
        "What has the caregiver recorded about sleep, lunch and dinner?",
        answer,
    )


def test_history_fallback_refuses_to_summarize_when_a_requested_topic_is_missing():
    context = [
        _event_line("sleep-event", "Mum slept poorly last night."),
        _event_line("lunch-event", "Mum barely touched her lunch today."),
    ]

    assert _care_history_extractive_answer(
        "What has the caregiver recorded about sleep, lunch and dinner?",
        context,
    ) is None


def test_history_topic_coverage_does_not_accept_generic_care_advice():
    question = "What has the caregiver recorded about sleep, lunch and dinner?"

    assert not _care_history_reply_covers_requested_topics(
        question,
        "It may help to discuss her daily routine with the care team.",
    )
    assert _care_history_reply_covers_requested_topics(
        question,
        "Sleep was poor, lunch intake was low, and dinner went better.",
    )


def test_history_fallback_is_limited_to_explicit_record_retrieval_questions():
    context = [
        _event_line("sleep-event", "Mum slept poorly last night."),
        _event_line("lunch-event", "Mum barely touched her lunch today."),
        _event_line("dinner-event", "Mum ate more at dinner."),
    ]

    assert _care_history_extractive_answer(
        "Should I change her medication?",
        context,
    ) is None
