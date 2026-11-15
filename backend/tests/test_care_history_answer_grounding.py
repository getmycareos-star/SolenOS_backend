import json

from app.api.collaboration import (
    _care_history_extractive_answer,
    _care_history_reply_covers_requested_topics,
    _lcr_missing_context_instruction,
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


def test_medication_dose_recall_quotes_the_uncertain_caregiver_report_without_inference():
    context = [
        _event_line(
            "medication-event",
            "I think the doctor changed Mum's medication yesterday, but I can't remember the new dose.",
        ),
    ]

    question = "What new dose did the doctor prescribe for Mum yesterday?"
    answer = _care_history_extractive_answer(question, context)

    assert answer is not None
    assert "I can't remember the new dose" in answer
    assert "caregiver report" in answer
    assert "not confirmation of the current prescription" in answer
    assert not __import__("re").search(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|ml)\b", answer, __import__("re").I)
    assert _care_history_reply_covers_requested_topics(question, answer, context)


def test_history_fallback_refuses_to_summarize_when_a_requested_topic_is_missing():
    context = [
        _event_line("sleep-event", "Mum slept poorly last night."),
        _event_line("lunch-event", "Mum barely touched her lunch today."),
    ]

    answer = _care_history_extractive_answer(
        "What has the caregiver recorded about sleep, lunch and dinner?",
        context,
    )

    assert answer is not None
    assert "Mum slept poorly last night" in answer
    assert "Mum barely touched her lunch today" in answer
    assert "I couldn't confirm dinner" in answer


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




def test_broad_history_answer_requires_saved_event_details():
    question = "What has the caregiver recorded?"
    context = [
        _event_line("sleep-event", "Mum slept poorly last night."),
    ]

    assert not _care_history_reply_covers_requested_topics(
        question,
        "I can help you think through her care routine.",
        context,
    )
    assert _care_history_reply_covers_requested_topics(
        question,
        "Mum slept poorly last night.",
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

    answer = _care_history_extractive_answer(
        "What has the caregiver recorded about sleep, lunch and dinner?",
        context,
    )

    assert answer is not None
    assert "Mum ate more at dinner" in answer
    assert "I couldn't confirm sleep and lunch" in answer
    assert "Mum slept poorly last night" not in answer
    assert "Mum barely touched her lunch today" not in answer


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



def test_incomplete_retrieval_never_turns_missing_results_into_absence_claims():
    instruction = _lcr_missing_context_instruction(
        {
            "retrieval_projection_complete": False,
            "page_limit_reached": True,
        },
        context_truncated=False,
    )

    assert "cannot confirm from the records retrieved" in instruction
    assert "do not say the information is not recorded" in instruction
    assert "additional saved history may need checking" in instruction


def test_complete_retrieval_uses_bounded_absence_language_without_claiming_nonoccurrence():
    instruction = _lcr_missing_context_instruction(
        {"retrieval_projection_complete": True, "page_limit_reached": False},
        context_truncated=False,
    )

    assert "not present in the retrieved care record" in instruction
    assert "do not infer that the event did not occur" in instruction
    assert "cannot confirm from the records retrieved" not in instruction


def test_prompt_truncation_marks_retrieval_as_incomplete():
    instruction = _lcr_missing_context_instruction(
        {"retrieval_projection_complete": True, "page_limit_reached": False},
        context_truncated=True,
    )

    assert "cannot confirm from the records retrieved" in instruction
    assert "additional saved history may need checking" in instruction
