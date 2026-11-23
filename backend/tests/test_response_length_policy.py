from app.services.response_length_policy import (
    count_response_words,
    is_critical_safety_message,
    is_self_harm_safety_message,
    is_greeting_message,
    resolve_response_length_policy,
    resolve_workflow_response_length_policy,
    response_exceeds_hard_maximum,
)


def test_first_time_and_returning_greetings_have_distinct_targets_and_maxima():
    first = resolve_response_length_policy("Hello", caregiver_stage="new")
    returning = resolve_response_length_policy("Hello", caregiver_stage="returning")
    assert (first.target_words, first.max_words) == (25, 40)
    assert (returning.target_words, returning.max_words) == (30, 55)
    assert resolve_response_length_policy("Hello again", caregiver_stage="returning").intent == "returning_greeting"


def test_explicit_caregiver_capacity_signals_use_low_capacity_response_budget():
    for message in (
        "I'm overwhelmed by everything today.",
        "I'm exhausted and can't keep up.",
        "I'm really angry and exhausted.",
        "I can't cope with all of this.",
        "I can't continue right now.",
        "I need a break.",
        "I just want a break.",
    ):
        policy = resolve_response_length_policy(message)
        assert policy.intent == "low_capacity_support", message
        assert (policy.target_words, policy.max_words) == (35, 60), message


def test_emotion_alone_does_not_override_a_caregiver_request_for_detail():
    detailed = resolve_response_length_policy(
        "I'm sad about how things are going. Please explain the full care history and timeline."
    )
    assert detailed.intent == "complex_care_history"
    assert (detailed.target_words, detailed.max_words) == (150, 250)

    for message in (
        "I'm lonely. What did the caregiver record about Mum's sleep?",
        "I'm frightened about the future. What was recorded at the last appointment?",
        "I'm angry about this. Explain what changed over time.",
        "I've been crying this morning.",
    ):
        policy = resolve_response_length_policy(message)
        assert policy.intent != "low_capacity_support", message


def test_inability_to_continue_and_explicit_pause_use_low_capacity_budget():
    for message in (
        "I can't continue right now.",
        "I cannot keep doing this.",
        "I need a pause.",
        "I don't want to talk about this anymore.",
        "Please stop for now.",
    ):
        policy = resolve_response_length_policy(message)
        assert policy.intent == "low_capacity_support", message
        assert (policy.target_words, policy.max_words) == (35, 60), message


def test_patient_emotion_is_not_mistaken_for_caregiver_distress():
    policy = resolve_response_length_policy("Mum has seemed sad and withdrawn this week.")
    assert policy.intent != "low_capacity_support"


def test_emotional_context_is_not_classified_as_a_greeting_or_acknowledgement():
    policy = resolve_response_length_policy("Hi. I'm exhausted. Mum has been awake all night again.")
    assert policy.intent == "low_capacity_support"
    assert (policy.target_words, policy.max_words) == (35, 60)


def test_complex_care_history_gets_a_larger_bounded_budget():
    policy = resolve_response_length_policy("Please explain the full care history and timeline.")
    assert policy.intent == "complex_care_history"
    assert (policy.target_words, policy.max_words) == (150, 250)
    assert policy.token_budget > resolve_response_length_policy("When was the last visit?").token_budget


def test_critical_safety_has_no_generic_word_ceiling():
    policy = resolve_response_length_policy("Mum is unresponsive and not breathing.")
    assert policy.intent == "critical_safety"
    assert policy.max_words is None
    assert not response_exceeds_hard_maximum("Call emergency services now. " + "Important detail. " * 80, policy)


def test_explicit_and_indirect_self_harm_language_is_critical_safety():
    for message in (
        "I want to die.",
        "I don't want to live anymore.",
        "I wish I were dead.",
        "Everyone would be better off without me.",
        "I can't keep myself safe tonight.",
        "I'm afraid I might hurt myself.",
        "Mum says she would be better off dead.",
    ):
        assert is_self_harm_safety_message(message), message
        assert is_critical_safety_message(message), message
        assert resolve_response_length_policy(message).intent == "critical_safety", message

    assert not is_self_harm_safety_message("I'm exhausted and need a break.")
    assert not is_self_harm_safety_message("I'm worried Mum may fall again.")


def test_word_counter_handles_apostrophes_and_punctuation():
    assert count_response_words("Mum's care record—today, not yesterday.") == 6


def test_hard_maximum_rejects_without_truncating():
    policy = resolve_response_length_policy("What does this document mean?")
    assert policy.max_words == 180
    assert response_exceeds_hard_maximum("word " * 181, policy)



def test_acknowledgement_is_not_misclassified_as_first_time_orientation():
    policy = resolve_response_length_policy("Thanks", caregiver_stage="new")
    assert policy.intent == "simple_acknowledgement"
    assert (policy.target_words, policy.max_words) == (10, 15)


def test_all_longer_caregiver_workflows_have_explicit_contracts():
    assert (resolve_response_length_policy("Prepare for Mum's appointment").target_words,
            resolve_response_length_policy("Prepare for Mum's appointment").max_words) == (150, 250)
    assert (resolve_response_length_policy("Prepare a care handoff").target_words,
            resolve_response_length_policy("Prepare a care handoff").max_words) == (150, 250)
    assert (resolve_response_length_policy("What does this document mean?").target_words,
            resolve_response_length_policy("What does this document mean?").max_words) == (100, 180)
    assert (resolve_response_length_policy("Has her sleep worsened over time?").target_words,
            resolve_response_length_policy("Has her sleep worsened over time?").max_words) == (120, 200)



def test_goodbye_and_thanks_are_acknowledgements_not_orientation_greetings():
    assert resolve_response_length_policy("Goodbye").intent == "simple_acknowledgement"
    assert resolve_response_length_policy("Good night").intent == "simple_acknowledgement"
    assert resolve_response_length_policy("Thanks", caregiver_stage="new").intent == "simple_acknowledgement"


def test_emergency_override_can_use_existing_deterministic_urgency_result():
    policy = resolve_response_length_policy(
        "Mum fell and is confused",
        critical_safety=True,
    )
    assert policy.intent == "critical_safety"
    assert policy.max_words is None


def test_explicit_workflow_hint_wins_over_ambiguous_prompt_wording():
    policy = resolve_response_length_policy(
        "Please help with this",
        intent_hint="care_handoff",
    )
    assert (policy.target_words, policy.max_words) == (150, 250)


def test_greeting_with_emotional_content_uses_low_capacity_support():
    policy = resolve_response_length_policy("Hello. I am overwhelmed and cannot cope.")
    assert policy.intent == "low_capacity_support"
    assert (policy.target_words, policy.max_words) == (35, 60)


def test_critical_terms_are_recognized_after_policy_regex_refactor():
    assert resolve_response_length_policy("She is unresponsive").intent == "critical_safety"
    assert resolve_response_length_policy("He is having a seizure").intent == "critical_safety"


def test_good_night_is_a_bounded_acknowledgement_not_a_greeting():
    policy = resolve_response_length_policy("Good night!")
    assert policy.intent == "simple_acknowledgement"
    assert (policy.target_words, policy.max_words) == (10, 15)


def test_greeting_with_there_is_classified_as_greeting():
    policy = resolve_response_length_policy("Hi there.")
    assert policy.intent == "first_time_greeting"



def test_shared_greeting_detector_matches_orientation_and_companion_boundaries():
    assert is_greeting_message("Hi!")
    assert is_greeting_message("Good morning, SolenOS")
    assert is_greeting_message("How are you today?")
    assert is_greeting_message("Hello again")
    assert is_greeting_message("Hello there SolenOS again")
    assert not is_greeting_message("Thanks")
    assert not is_greeting_message("Goodbye")
    assert not is_greeting_message("Good night")
    assert not is_greeting_message("Hi, I am exhausted.")



def test_appointment_keyword_alone_does_not_trigger_long_preparation_contract():
    policy = resolve_response_length_policy("Was the appointment on Tuesday?")
    assert policy.intent == "simple_factual_answer"
    assert (policy.target_words, policy.max_words) == (35, 60)


def test_invalid_explicit_workflow_hint_fails_loudly():
    import pytest

    with pytest.raises(ValueError, match="Unsupported response-length intent hint"):
        resolve_response_length_policy("anything", intent_hint="unknown_workflow")


def test_explicit_appointment_preparation_phrase_uses_longer_briefing_contract():
    policy = resolve_response_length_policy("Please create an appointment preparation summary.")
    assert policy.intent == "appointment_preparation"
    assert (policy.target_words, policy.max_words) == (150, 250)


def test_questions_for_the_doctor_use_appointment_preparation_contract():
    policy = resolve_response_length_policy("What questions should I bring for the doctor?")
    assert policy.intent == "appointment_preparation"
    assert (policy.target_words, policy.max_words) == (150, 250)

def test_word_count_matches_whitespace_delimited_frontend_semantics():
    assert count_response_words("Mum's well-being is improving.") == 4
    assert count_response_words("  one\ntwo\nthree  ") == 3



def test_critical_safety_classifier_is_shared_and_covers_emergency_language():
    for message in (
        "Mum is having a stroke.",
        "He may be having a heart attack.",
        "She is choking.",
        "He is suicidal.",
        "There is severe bleeding.",
        "She is not waking up.",
    ):
        assert is_critical_safety_message(message), message
        policy = resolve_response_length_policy(message)
        assert policy.intent == "critical_safety"
        assert policy.max_words is None
        assert policy.token_budget == 1024


def test_historical_diagnosis_mentions_do_not_disable_ordinary_length_contracts():
    for message in (
        "What does Mum's history of stroke mean for her care?",
        "The record mentions a seizure in 2019.",
        "Can you summarize the previous heart attack admission?",
    ):
        assert not is_critical_safety_message(message), message
        assert resolve_response_length_policy(message).max_words is not None



def test_every_response_intent_has_a_bounded_generation_budget():
    cases = (
        ("Hello", "new", None, 112),
        ("Hello", "returning", None, 160),
        ("Thanks", None, None, 64),
        ("When was the last visit?", None, None, 192),
        ("Explain what this means.", None, None, 448),
        ("Please show the full care history and timeline.", None, None, 768),
        ("Has her sleep worsened over time?", None, None, 640),
        ("What does this document mean?", None, None, 512),
        ("Prepare an appointment briefing.", None, None, 768),
        ("Prepare a care handoff.", None, None, 768),
        ("I'm exhausted and overwhelmed.", None, None, 192),
        ("Mum is not breathing.", None, None, 1024),
    )
    for message, stage, hint, expected_budget in cases:
        policy = resolve_response_length_policy(message, caregiver_stage=stage, intent_hint=hint)
        assert policy.token_budget == expected_budget, message



def test_every_enabled_qwen_workflow_has_an_explicit_response_contract():
    from app.services.qwen_workflows import ENABLED_WORKFLOWS

    for workflow in ENABLED_WORKFLOWS:
        policy = resolve_workflow_response_length_policy(workflow, "Please help with this.")
        assert policy.token_budget >= 48, workflow
        assert policy.token_budget <= 1024, workflow
        assert policy.max_words is None or policy.max_words >= 15, workflow


def test_qwen_workflow_contracts_select_the_expected_caregiver_detail_level():
    expected = {
        "document": (100, 180),
        "living_care_record": (150, 250),
        "uncertainty_contradictions": (150, 250),
        "care_situation_change": (120, 200),
        "returning_caregiver": (120, 200),
        "appointment": (150, 250),
        "handoff": (150, 250),
        "reminders_notifications": (35, 60),
        "caregiver_strain_support": (35, 60),
        "critical_information_completeness": (150, 250),
    }
    for workflow, limits in expected.items():
        policy = resolve_workflow_response_length_policy(workflow, "Please help with this.")
        assert (policy.target_words, policy.max_words) == limits, workflow


def test_workflow_policy_still_prioritizes_critical_safety_and_caregiver_distress():
    critical = resolve_workflow_response_length_policy("appointment", "Mum is not breathing.")
    overwhelmed = resolve_workflow_response_length_policy(
        "professional_summary",
        "I'm exhausted and overwhelmed.",
    )
    assert critical.intent == "critical_safety"
    assert critical.max_words is None
    assert overwhelmed.intent == "low_capacity_support"
    assert (overwhelmed.target_words, overwhelmed.max_words) == (35, 60)


def test_patient_emotion_does_not_trigger_caregiver_low_capacity_response():
    for message in (
        "Mum was overwhelmed after lunch.",
        "My dad has been exhausted all week.",
        "Mum is tired after lunch.",
        "She seems drained after the appointment.",
        "She seems frightened at night.",
        "Mum was panicking before the appointment.",
        "He is terrified about the move.",
        "The care recipient felt lonely yesterday.",
    ):
        policy = resolve_response_length_policy(message)
        assert policy.intent != "low_capacity_support", message


def test_caregiver_distress_still_wins_when_patient_emotion_is_also_reported():
    for message in (
        "Mum was overwhelmed after lunch, and I'm exhausted from trying to help.",
        "Mum has seemed sad this week, and I've been crying.",
        "She is frightened at night, and I just want a break.",
        "Mum was overwhelmed, and I am too.",
        "My dad is exhausted, and I feel the same.",
    ):
        policy = resolve_response_length_policy(message)
        assert policy.intent == "low_capacity_support", message


def test_indirect_self_harm_language_uses_unbounded_critical_safety_policy():
    for message in (
        "I wish I were dead.",
        "I wish I was dead.",
        "Everyone would be better off without me.",
        "There is no reason to live.",
        "Life isn't worth living.",
        "I do not want to live.",
        "I can't keep myself safe.",
        "I cannot keep myself safe.",
        "I'm thinking about suicide.",
        "I might hurt myself.",
        "I'm afraid I might hurt someone.",
        "Mum wants to die.",
        "Dad wants to kill himself.",
        "She told me she wants to die.",
        "He wishes he were dead.",
        "Mum is hurting herself.",
        "They cannot keep themselves safe.",
    ):
        assert is_critical_safety_message(message), message
        policy = resolve_response_length_policy(message)
        assert policy.intent == "critical_safety", message
        assert policy.max_words is None, message


def test_single_word_caregiver_emotions_use_low_capacity_support():
    for message in ("Sad", "Angry.", "Frustrated", "Overwhelmed", "Furious!", "Tired", "Drained"):
        policy = resolve_response_length_policy(message)
        assert policy.intent == "low_capacity_support", message
