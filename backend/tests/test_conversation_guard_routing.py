"""Regression tests for the conversational guard routing bug.

The engine guarded every model response as an insight, which demands a
"summary" field. The conversation prompt asks for "reply". Every conversational
turn was therefore rejected with "model returned no summary" - a reason the
caller reads as "the model is unavailable" - so the product answered nothing at
all while every test still passed, because the suite disables the local model
and never exercised a real model response.

These tests pin the routing so the failure cannot return silently.
"""

import pytest

from app.core.llm_enums import LLMTask, LLMReasoningMode
from app.services.llm.engine import LocalReasoningEngine


class _FakeClient:
    """A model that answers the way the conversation prompt asks it to."""

    def __init__(self, payload, ok=True, status="ok"):
        self.payload = payload
        self.ok = ok
        self.status = status
        self.calls = []

    def generate_json(self, prompt, payload):
        self.calls.append((prompt, payload))
        return self.ok, self.payload, str(self.payload), {"status": self.status, "latency_ms": 10}

    def is_available(self):
        return True, None


CONVERSATION_REPLY = {
    "reply": "Alex is on Donepezil 10mg.",
    "stage": "in_care_context",
    "grounded_claims": ["e1"],
    "possible_context": None,
    "confidence": 0.8,
    "safety_note": None,
}


@pytest.fixture()
def engine(db):
    eng = LocalReasoningEngine(db=db, client=_FakeClient(dict(CONVERSATION_REPLY)))
    eng.enabled = True
    return eng


def test_a_conversational_reply_is_not_rejected_for_having_no_summary(engine, db):
    """The exact failure: a valid reply discarded because it had no 'summary'."""
    result = engine.reason(
        task=LLMTask.CONVERSATION_REPLY,
        person_id="p1",
        payload={"caregiver_said": "What medication is he on?"},
        evidence_ids=["e1"],
        mode=LLMReasoningMode.LLM_DRAFT,
    )
    assert result is not None, "conversation reply was discarded"
    assert result.summary == "Alex is on Donepezil 10mg."


def test_the_reply_text_survives_into_the_record(engine, db):
    """The reply is stored in the summary column, so it must not be empty."""
    result = engine.reason(
        task=LLMTask.CONVERSATION_REPLY,
        person_id="p1",
        payload={"caregiver_said": "What medication is he on?"},
        evidence_ids=["e1"],
        mode=LLMReasoningMode.LLM_DRAFT,
    )
    assert result.summary.strip(), "reply text was lost"


def test_a_grounded_conversational_reply_keeps_its_evidence_and_confidence(engine, db):
    result = engine.reason(
        task=LLMTask.CONVERSATION_REPLY,
        person_id="p1",
        payload={"caregiver_said": "What medication is he on?"},
        evidence_ids=["e1"],
        mode=LLMReasoningMode.LLM_DRAFT,
    )
    assert result.evidence_ids == ["e1"]
    assert result.confidence == pytest.approx(0.8)


def test_an_insight_is_still_guarded_as_an_insight(db):
    """The routing must not weaken the path that everything else uses.

    A change like this can easily turn a fix into a hole, so the insight path is
    pinned to the summary shape it expects.
    """
    payload = {
        "summary": "Alex was started on Donepezil.",
        "evidence_ids": ["e1"],
        "confidence": 0.7,
        "possible_context": "Whether it is helping is not established.",
    }
    eng = LocalReasoningEngine(db=db, client=_FakeClient(payload))
    eng.enabled = True

    result = eng.reason(
        task=LLMTask.WHAT_MATTERS,
        person_id="p1",
        payload={},
        evidence_ids=["e1"],
        mode=LLMReasoningMode.LLM_ASSISTED,
    )
    assert result is not None
    assert result.summary == "Alex was started on Donepezil."
    assert result.evidence_ids == ["e1"]


def test_an_insight_missing_its_summary_is_still_rejected(db):
    """The insight path must not be loosened by the conversation fix."""
    eng = LocalReasoningEngine(db=db, client=_FakeClient({"reply": "text only"}))
    eng.enabled = True

    result = eng.reason(
        task=LLMTask.WHAT_MATTERS,
        person_id="p1",
        payload={},
        evidence_ids=["e1"],
        mode=LLMReasoningMode.LLM_ASSISTED,
    )
    assert result is None


def test_the_audit_trail_records_the_answered_turn(engine, db):
    """A reply that survives must be auditable, including the rejected ones."""
    from app.models.llm import LLMCall

    engine.reason(
        task=LLMTask.CONVERSATION_REPLY,
        person_id="p1",
        payload={"caregiver_said": "What medication is he on?"},
        evidence_ids=["e1"],
        mode=LLMReasoningMode.LLM_DRAFT,
    )
    db.commit()
    call = db.query(LLMCall).order_by(LLMCall.id.desc()).first()
    assert call is not None
    assert "no summary" not in str(call.guard_reasons)


class TestFallbackWording:
    def test_a_model_outage_is_not_reported_as_a_gap_in_the_record(self):
        """These are different situations and a caregiver must be able to tell.

        Telling someone their mother's medication history is missing because a
        local process timed out is both more alarming and false.
        """
        from app.core.care_context_enums import ConversationStage
        from app.services.conversation import _fallback_reply

        reply = _fallback_reply("What medication is he on?", ConversationStage.IN_CARE_CONTEXT)
        assert "record" in reply
        assert "not answered" in reply or "reach my local reasoning" in reply


class TestFalseReassurance:
    """A record cannot support reassurance about a person's state.

    The marker list caught "no need to worry" but let "he seems fine" and "it is
    not an emergency" through. A model that is blocked on one phrasing simply
    picks another, so a guard keyed to literal wording stops being a guard.
    """

    def _usable(self, reply: str) -> bool:
        from app.services.llm.trust_guard import TrustGuard

        guard = TrustGuard(permitted_evidence_ids=["e1", "e2"], require_evidence=False)
        return guard.guard_conversation(
            {"reply": reply, "grounded_claims": ["e1", "e2"], "confidence": 0.9}
        ).is_usable

    @pytest.mark.parametrize(
        "reply",
        [
            "No, it's not an emergency. He seems fine and has been taking his medication as prescribed.",
            "He seems fine.",
            "No, it's not an emergency.",
            "There is no need to worry.",
            "Everything appears stable.",
            "He looks stable to me.",
            "Nothing to report.",
            "You can take no action.",
        ],
    )
    def test_reassurance_is_refused_however_it_is_phrased(self, reply):
        assert self._usable(reply) is False, "reassurance slipped through: " + reply

    @pytest.mark.parametrize(
        "reply",
        [
            "The record does not say how he has been sleeping.",
            "I do not know whether he took the evening dose.",
            "She was noted as unsettled on Tuesday, and I cannot tell you why.",
        ],
    )
    def test_honest_uncertainty_is_still_allowed(self, reply):
        """The guard must not slide into refusing every careful answer.

        Blocking reassurance is only safe if the honest alternative survives.
        """
        assert self._usable(reply) is True, "an honest answer was wrongly blocked: " + reply


class TestDiagnosticPatterns:
    """Assertions must be blocked; denials must survive.

    The pattern "condition is <word>" was swallowing the negation, so "her
    condition is not established" was rejected as a diagnosis - the guard
    discarded the most honest sentence the model can produce. In the other
    direction, "This is a stroke" matched nothing at all and passed, because
    the older "this is ..." rule required an adjective like "severe".
    """

    def _usable(self, reply: str) -> bool:
        from app.services.llm.trust_guard import TrustGuard

        guard = TrustGuard(permitted_evidence_ids=["e1"], require_evidence=False)
        return guard.guard_conversation({"reply": reply, "grounded_claims": []}).is_usable

    @pytest.mark.parametrize(
        "reply",
        [
            "This is a stroke.",
            "This is a heart attack.",
            "She is having a seizure.",
            "He is septic.",
            "She is hypoglycemic.",
            "He has Alzheimer's disease.",
            "She is suffering from dementia.",
            "His condition is dementia.",
        ],
    )
    def test_a_bare_diagnostic_assertion_is_refused(self, reply):
        assert self._usable(reply) is False, "diagnosis slipped through: " + reply

    @pytest.mark.parametrize(
        "reply",
        [
            "The evidence does not establish a cause for her condition.",
            "Her condition is not established by the record.",
            "We do not know what his condition is yet.",
            "No cause for this change has been confirmed.",
            "Nothing in the record establishes a diagnosis.",
            "The record does not say what is causing this.",
        ],
    )
    def test_an_hedged_denial_survives(self, reply):
        assert self._usable(reply) is True, "an honest denial was blocked: " + reply


class TestPromptContract:
    def test_the_prompt_tells_the_model_to_copy_ids_exactly(self):
        """A 1.5b model returns [] when not shown what an id looks like."""
        from app.services.llm.prompts import get_prompt

        prompt = get_prompt("conversation_reply")
        assert "character for character" in prompt
        # It must be told that an uncited answer is discarded, because that is
        # the behaviour being relied on.
        assert "discarded" in prompt

    def test_the_prompt_forbids_vaguening_specific_names(self):
        from app.services.llm.prompts import get_prompt

        prompt = get_prompt("conversation_reply")
        assert "Donepezil" in prompt
