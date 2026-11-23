"""Deterministic Companion interaction regressions across 159 distinct caregiver messages.

This corpus validates only deterministic routing and response constraints. It is not
real-model acceptance, clinical validation, PostgreSQL authorization testing, or
browser acceptance; those remain separate gates.
"""
import pytest

from app.api import collaboration
from app.services.response_length_policy import (
    is_critical_safety_message,
    is_low_capacity_caregiver_message,
    is_self_harm_safety_message,
)


def _cases():
    cases = []

    def add(group, messages, expected):
        for index, message in enumerate(messages, 1):
            cases.append((f"{group}-{index:02d}", group, message, expected))

    add("greeting", [
        "Hello", "Hi", "Hey", "Good morning", "Good afternoon", "Good evening",
        "Hello there", "Hi there", "Hey there", "Good morning!", "Good afternoon.",
        "Good evening!",
    ], "greeting")

    add("sadness_grief_loneliness", [
        "I feel sad today.", "I'm really sad about Mum's decline.",
        "I have been crying about how things have changed.",
        "I can't stop crying today.", "I feel like crying when I see her struggle.",
        "I miss her so much.", "I'm grieving the person Dad used to be.",
        "I feel like I'm losing him.", "Since she died, ordinary days feel difficult.",
        "I can't believe he is gone.",
        "I feel alone in caring for Dad.", "I feel completely lonely.",
        "I have nobody to talk to about this.", "There is nobody I can talk to.",
        "No one understands what this is like.",
    ], "distress")

    add("anger_frustration", [
        "I'm angry with the doctor about what happened.",
        "I am furious at the hospital.",
        "I'm fed up with the care team.",
        "I'm angry at SolenOS for losing my information.",
        "I hate how the doctor handled this.",
        "This makes me furious.",
        "I'm really frustrated with being asked again.",
        "I'm frustrated with repeating myself.",
        "I already told you this yesterday.",
        "I have already told you what happened.",
        "Why do I have to repeat the same details?",
        "You keep asking me the same question.",
        "You are not listening to what I said.",
        "You keep getting this wrong.",
        "This system is useless and keeps failing.",
    ], "anger_or_continuity")

    add("overwhelm", [
        "I'm overwhelmed today.", "I feel overwhelmed by all of this.",
        "I'm exhausted from caring for Mum.", "I am completely drained.",
        "I feel worn out and cannot think clearly.",
        "I'm burnt out from doing this every day.",
        "I'm burned out and need a break.",
        "I can't take this anymore.", "I cannot take this today.",
        "I've had enough for now.", "I have had enough and need to stop.",
        "I just want a break from caregiving.",
        "I'm too tired to keep talking.",
        "I need to pause this conversation.",
        "Please stop for now.",
    ], "low_capacity")

    add("fear_guilt_uncertainty", [
        "I'm afraid about what happens as Mum's dementia progresses.",
        "I am scared about the future.",
        "I'm frightened about what will happen next.",
        "I don't know what to expect.",
        "I don't know if this is normal.",
        "I'm not sure whether this medication is safe.",
        "I feel like I'm failing her.",
        "I should be doing more for Dad.",
        "I lost my temper today.",
        "I don't think I'm a good caregiver.",
        "I blame myself for what happened.",
        "I feel really guilty about yesterday.",
    ], "fear_or_self_blame")

    add("ordinary_complex_questions", [
        "What is the difference between vascular dementia and Alzheimer's disease?",
        "How can I prepare for Mum's next appointment?",
        "What questions should I ask the neurologist?",
        "Can you explain why routines can help with dementia care?",
        "How do I make a room easier to navigate?",
        "What does this discharge instruction mean?",
        "Can you summarize the appointment notes I uploaded?",
        "What changed in Dad's sleep over the last month?",
        "Compare the last two recorded mobility observations.",
        "What information is missing from the care history?",
        "How can I organize a handoff for the next caregiver?",
        "What should I include in a medication question for the pharmacist?",
        "Can you explain the difference between a reported event and a confirmed instruction?",
        "What does the record say about the last hospital visit?",
        "How can I make mealtimes less stressful?",
        "What are common communication challenges in dementia?",
        "Can you help me understand this care plan?",
        "What should I ask before the next home-care visit?",
        "How do I document a change in daily routine?",
        "Can you explain the options without choosing for me?",
    ], "ordinary")

    add("followups_continuity", [
        "No, that's not what happened.",
        "That isn't what happened yesterday.",
        "You got that wrong.",
        "That's incorrect; I meant Tuesday.",
        "To correct that, she was awake until midnight.",
        "I meant the appointment next week, not yesterday.",
        "I already told you the date.",
        "I said this already.",
        "You forgot the detail I gave you.",
        "Stop asking me to repeat the medication name.",
        "I have already told you about the fall.",
        "Why are you asking me that again?",
        "You keep getting the time wrong.",
        "This is frustrating because I explained it before.",
        "Please use the information I already gave you.",
    ], "correction_or_continuity")

    add("missing_conflicting_information", [
        "The record does not show when the change started.",
        "I have two different dates for the appointment.",
        "The discharge paper and my notes do not match.",
        "One note says she was eating; another says she was not.",
        "I cannot tell which report is the latest.",
        "The document does not include a medication dose.",
        "I do not know who gave that instruction.",
        "The source of this statement is missing.",
        "The two accounts describe different times.",
        "I cannot confirm whether that visit happened.",
        "The record has no baseline for comparison.",
        "The uploaded page is difficult to read.",
        "There may be another explanation for this change.",
        "The earlier entry was corrected, but I need to see both versions.",
        "I don't know whether this information is current.",
    ], "information_boundary")

    add("medical_emergency", [
        "Mum is not breathing.",
        "Dad is having trouble breathing.",
        "She is unconscious.",
        "He is unresponsive and cannot wake up.",
        "Her face is drooping on one side.",
        "He has sudden weakness on one side.",
        "Her speech suddenly became slurred.",
        "He has severe chest pain now.",
        "She is choking right now.",
        "He is having a seizure.",
        "There may have been an overdose.",
        "She has severe bleeding.",
        "His lips are blue and he is gasping.",
        "I think she is having a stroke.",
        "There is immediate danger.",
    ], "emergency")

    add("lcr_retrieval_and_persistence", [
        "Please use the care history I already shared.",
        "What does the saved record say about her last fall?",
        "Show me what was recorded and what is still uncertain.",
        "The earlier entry is wrong; she did not fall on Monday.",
        "I meant that she missed lunch, not breakfast.",
        "Please correct the time in this conversation, but do not save it yet.",
        "That report was withdrawn; do not use it as current information.",
        "The document says one thing and Daily Care says another.",
        "What was reported by me and what was confirmed by the nurse?",
        "Can you compare the event date with when it was entered?",
        "I want to review the source before accepting this.",
        "Do not add this conversation to her care record.",
        "This is a possibility, not something we know happened.",
        "Please keep the earlier version visible when showing the correction.",
        "What information is actually accepted in the care record?",
    ], "lcr_boundary")

    add("correction_and_privacy", [
        "That correction is only for this conversation.",
        "Do not save this as a care fact.",
        "Please do not change the Living Care Record.",
        "I am talking about Dad, not Mum.",
        "Those are two different people; keep their records separate.",
        "Do not use my sister's care details when answering about Dad.",
        "The information belongs to another person's record.",
        "I am correcting my wording, not confirming a new medical fact.",
        "Keep this as something I reported, not a verified instruction.",
        "Please stop and do not make any record changes.",
    ], "privacy_boundary")

    return cases


CASES = _cases()


@pytest.mark.parametrize("case_id,group,message,expected", CASES, ids=[c[0] for c in CASES])
def test_companion_interaction_scenario_has_expected_deterministic_boundary(
    case_id, group, message, expected
):
    guidance = collaboration._caregiver_interaction_guidance(message)

    if expected == "greeting":
        assert collaboration._is_casual_greeting(message), case_id
        assert not guidance, {case_id: guidance}
    elif expected == "distress":
        assert any(marker in guidance for marker in ("DISTRESS RESPONSE", "GRIEF RESPONSE", "LONELINESS RESPONSE")), {case_id: guidance}
        if "miss her" in message.casefold() or "grieving" in message.casefold() or "losing him" in message.casefold() or "died" in message.casefold() or "gone" in message.casefold():
            assert "GRIEF RESPONSE" in guidance, {case_id: guidance}
        if "alone" in message.casefold() or "lonely" in message.casefold() or "nobody" in message.casefold() or "no one" in message.casefold():
            assert "LONELINESS RESPONSE" in guidance, {case_id: guidance}
    elif expected == "anger_or_continuity":
        assert "ANGER RESPONSE" in guidance or "CONTINUITY REPAIR" in guidance, {case_id: guidance}
    elif expected == "low_capacity":
        assert is_low_capacity_caregiver_message(message), case_id
        assert "LOW-CAPACITY RESPONSE" in guidance, {case_id: guidance}
        if "stop" in message.casefold() or "pause" in message.casefold():
            assert "PAUSE REQUEST" in guidance, {case_id: guidance}
    elif expected == "fear_or_self_blame":
        assert ("FEAR AND UNCERTAINTY RESPONSE" in guidance) or ("SELF-BLAME RESPONSE" in guidance), {case_id: guidance}
    elif expected == "ordinary":
        assert not is_critical_safety_message(message), case_id
        assert not is_low_capacity_caregiver_message(message), case_id
        assert not any(
            marker in guidance
            for marker in (
                "DISTRESS RESPONSE", "LOW-CAPACITY RESPONSE", "ANGER RESPONSE",
                "GRIEF RESPONSE", "SELF-BLAME RESPONSE", "FEAR AND UNCERTAINTY RESPONSE",
            )
        ), {case_id: guidance}
    elif expected == "correction_or_continuity":
        assert ("CORRECTION HANDLING" in guidance) or ("CONTINUITY REPAIR" in guidance), {case_id: guidance}
    elif expected == "information_boundary":
        assert not is_critical_safety_message(message), case_id
        assert not any(
            marker in guidance
            for marker in ("DISTRESS RESPONSE", "LOW-CAPACITY RESPONSE", "ANGER RESPONSE")
        ), {case_id: guidance}
    elif expected == "emergency":
        assert is_critical_safety_message(message), case_id
        emergency_reply, _ = collaboration._emergency_response_for_message(message)
        assert "emergency" in emergency_reply.casefold() or "urgent medical help" in emergency_reply.casefold(), case_id
    elif expected == "lcr_boundary":
        if "correct" in message.casefold() or "wrong" in message.casefold() or "meant" in message.casefold():
            assert "CORRECTION HANDLING" in guidance, {case_id: guidance}
        if "do not add" in message.casefold():
            assert "conversation responses do not write care facts" not in guidance
    elif expected == "privacy_boundary":
        assert not is_critical_safety_message(message), case_id
        if "correcting" in message.casefold():
            assert "CORRECTION HANDLING" in guidance, {case_id: guidance}
        else:
            assert "conversation responses do not write care facts" not in guidance


def test_companion_interaction_corpus_has_exact_minimum_distribution():
    expected = {
        "greeting": 12,
        "sadness_grief_loneliness": 15,
        "anger_frustration": 15,
        "overwhelm": 15,
        "fear_guilt_uncertainty": 12,
        "ordinary_complex_questions": 20,
        "followups_continuity": 15,
        "missing_conflicting_information": 15,
        "medical_emergency": 15,
        "lcr_retrieval_and_persistence": 15,
        "correction_and_privacy": 10,
    }
    actual = {}
    for _, group, _, _ in CASES:
        actual[group] = actual.get(group, 0) + 1
    assert len(CASES) == 159
    assert actual == expected
    assert len({case[2] for case in CASES}) == 159
