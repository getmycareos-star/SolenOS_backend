import json

from app.api.collaboration import (
    _care_history_extractive_answer,
    _care_history_reply_covers_requested_topics,
)


def _event_line(
    event_id: str,
    description: str,
    *,
    status: str = "recorded",
    caregiver_id: str | None = "caregiver-1",
) -> str:
    return "Shared LCR context: " + json.dumps({
        "source_type": "events",
        "record": {
            "id": event_id,
            "description": description,
            "status": status,
            "created_by_caregiver_id": caregiver_id,
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
        context,
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
    context = [
        _event_line("sleep-event", "Mum slept poorly last night and seemed more unsettled."),
        _event_line("lunch-event", "Mum barely touched her lunch today."),
        _event_line("dinner-event", "Mum was calmer and ate more at dinner."),
    ]

    assert not _care_history_reply_covers_requested_topics(
        question,
        "It may help to discuss her daily routine with the care team.",
        context,
    )
    assert _care_history_reply_covers_requested_topics(
        question,
        "Sleep was unsettled, Mum barely touched lunch, and she ate a little more at dinner.",
        context,
    )



def test_history_fallback_excludes_pending_and_unattributed_events():
    context = [
        _event_line(
            "pending-sleep",
            "Mum slept poorly last night.",
            status="pending_review",
        ),
        _event_line(
            "unattributed-lunch",
            "Mum barely touched her lunch today.",
            caregiver_id=None,
        ),
        _event_line(
            "recorded-dinner",
            "Mum ate more at dinner.",
        ),
    ]

    assert _care_history_extractive_answer(
        "What has the caregiver recorded about sleep, lunch and dinner?",
        context,
    ) is None


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
