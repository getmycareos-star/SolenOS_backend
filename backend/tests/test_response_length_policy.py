from app.services.response_length_policy import (
    count_response_words,
    is_greeting_message,
    resolve_response_length_policy,
    response_exceeds_hard_maximum,
)


def test_first_time_and_returning_greetings_have_distinct_targets_and_maxima():
    first = resolve_response_length_policy("Hello", caregiver_stage="new")
    returning = resolve_response_length_policy("Hello", caregiver_stage="returning")
    assert (first.target_words, first.max_words) == (25, 40)
    assert (returning.target_words, returning.max_words) == (30, 55)


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


def test_good_night_uses_the_same_bounded_greeting_contract():
    policy = resolve_response_length_policy("Good night!")
    assert policy.intent == "first_time_greeting"
    assert (policy.target_words, policy.max_words) == (25, 40)


def test_greeting_with_there_is_classified_as_greeting():
    policy = resolve_response_length_policy("Hi there.")
    assert policy.intent == "first_time_greeting"



def test_shared_greeting_detector_matches_orientation_and_companion_boundaries():
    assert is_greeting_message("Hi!")
    assert is_greeting_message("Good morning, SolenOS")
    assert is_greeting_message("How are you today?")
    assert not is_greeting_message("Thanks")
    assert not is_greeting_message("Goodbye")
    assert not is_greeting_message("Hi, I am exhausted.")
