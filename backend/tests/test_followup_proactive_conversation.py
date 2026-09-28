from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.care_context_enums import (
    ConversationStage,
    FollowUpTrigger,
)
from app.services.behavior import BehaviorEngine
from app.services.care_common import utc_now
from app.services.conversation import ConversationEngine
from app.services.follow_up import FollowUpEngine
from app.services.llm import LocalReasoningEngine
from app.services.proactive import ProactiveEngine, _in_quiet_hours
from app.services.trajectory import TrajectoryEngine
from llm_fakes import FakeOllamaClient, guarded_response


def engine_with(db, *responses):
    client = FakeOllamaClient(list(responses))
    local = LocalReasoningEngine(db=db, client=client)
    local.enabled = True
    return local, client


# ----------------------------------------------------------------------
# Follow-ups
# ----------------------------------------------------------------------


class TestFollowUpScheduling:
    def test_default_interval_varies_by_trigger(self, db, person):
        model = FollowUpEngine(db)
        safety = model.schedule(
            person_id=person.id, what_to_recheck="Any bruising", trigger=FollowUpTrigger.SAFETY
        )
        trajectory = model.schedule(
            person_id=person.id, what_to_recheck="Is the weight still going", trigger=FollowUpTrigger.TRAJECTORY
        )
        db.commit()
        assert safety.interval_days < trajectory.interval_days
        assert safety.due_at < trajectory.due_at

    def test_an_unscheduled_default_says_itself(self, db, person):
        model = FollowUpEngine(db)
        follow_up = model.schedule(
            person_id=person.id, what_to_recheck="Something", trigger=FollowUpTrigger.UNCERTAINTY
        )
        db.commit()
        assert "default rather than a judgement" in follow_up.possible_context

    def test_a_recorded_reason_clears_the_default_note(self, db, person):
        model = FollowUpEngine(db)
        follow_up = model.schedule(
            person_id=person.id,
            what_to_recheck="Something",
            trigger=FollowUpTrigger.UNCERTAINTY,
            why_open="Two readings disagree",
        )
        db.commit()
        assert follow_up.possible_context is None

    def test_empty_recheck_is_refused(self, db, person):
        with pytest.raises(ValueError):
            FollowUpEngine(db).schedule(person_id=person.id, what_to_recheck="   ")

    def test_due_and_overdue_are_separated(self, db, person):
        model = FollowUpEngine(db)
        model.schedule(
            person_id=person.id,
            what_to_recheck="Overdue thing",
            due_at=utc_now() - timedelta(days=2),
        )
        model.schedule(
            person_id=person.id,
            what_to_recheck="Future thing",
            due_at=utc_now() + timedelta(days=5),
        )
        db.commit()
        overdue = model.overdue(person.id)
        assert [f.what_to_recheck for f in overdue] == ["Overdue thing"]
        assert len(model.due(person.id)) == 1
        assert len(model.due(person.id, grace_days=10)) == 2

    def test_completing_twice_is_refused(self, db, person, caregiver):
        model = FollowUpEngine(db)
        follow_up = model.schedule(person_id=person.id, what_to_recheck="Something")
        db.commit()
        model.complete(follow_up.id, "Checked, no change", caregiver.id)
        db.commit()
        with pytest.raises(ValueError):
            model.complete(follow_up.id, "again")

    def test_a_completed_follow_up_cannot_be_rescheduled(self, db, person):
        model = FollowUpEngine(db)
        follow_up = model.schedule(person_id=person.id, what_to_recheck="Something")
        db.commit()
        model.complete(follow_up.id, "done")
        db.commit()
        with pytest.raises(ValueError):
            model.reschedule(follow_up.id, utc_now() + timedelta(days=1))

    def test_rescheduling_records_why(self, db, person):
        model = FollowUpEngine(db)
        follow_up = model.schedule(person_id=person.id, what_to_recheck="Something")
        db.commit()
        original_due = follow_up.due_at
        moved = model.reschedule(
            follow_up.id, utc_now() + timedelta(days=30), "Waiting for the appointment"
        )
        db.commit()
        assert "Waiting for the appointment" in moved.why_open
        assert moved.due_at > original_due

    def test_uncertain_trajectories_get_an_earlier_recheck(self, db, person):
        model = FollowUpEngine(db)
        trajectory_engine = TrajectoryEngine(db)
        for index in range(3):
            trajectory_engine.record_point(
                person_id=person.id,
                subject="weight",
                value=80 - index * 2,
                observed_at=f"2026-02-0{index + 1}",
            )
        trajectory = trajectory_engine.track(person.id, "weight")
        db.commit()
        follow_up = model.schedule_for_trajectory(person.id, trajectory.id)
        db.commit()
        assert follow_up.interval_days <= 21
        assert "still moving in the same direction" in follow_up.what_to_recheck

    def test_sweep_does_not_duplicate_existing_rechecks(self, db, person):
        model = FollowUpEngine(db)
        trajectory_engine = TrajectoryEngine(db)
        for index in range(3):
            trajectory_engine.record_point(
                person_id=person.id,
                subject="weight",
                value=80 - index * 2,
                observed_at=f"2026-02-0{index + 1}",
            )
        trajectory_engine.track(person.id, "weight")
        db.commit()

        first = model.schedule_from_open_state(person.id)
        db.commit()
        second = model.schedule_from_open_state(person.id)
        db.commit()
        assert first, "a moving trajectory with no re-check scheduled is a gap"
        assert second == [], "sweeping again must not pile up duplicate re-checks"

    def test_view_reports_days_until_due(self, db, person):
        model = FollowUpEngine(db)
        model.schedule(
            person_id=person.id,
            what_to_recheck="Something",
            due_at=utc_now() + timedelta(days=3),
        )
        db.commit()
        view = model.schedule_view(person.id)
        # days_between truncates, so "3 days from now" can read as 2 or 3
        # depending on where in the day the clock falls. Either is honest.
        assert 2 <= view["scheduled"][0]["days_until_due"] <= 3

    def test_rationale_is_honest_without_a_model(self, db, person):
        result = FollowUpEngine(db).rationale_for(person.id, "Is the weight still going?")
        assert result["rechecks"] == []
        assert "No local reasoning available" in result["possible_context"]


# ----------------------------------------------------------------------
# Proactive engagement
# ----------------------------------------------------------------------


class TestProactiveDefaultsToSilence:
    def test_nothing_happens_without_a_model(self, db, person, evidence):
        evidence("e1", "Something was recorded.")
        # An overdue re-check is a real reason to wonder about the caregiver,
        # but wondering is not the same as contacting them.
        FollowUpEngine(db).schedule(
            person_id=person.id,
            what_to_recheck="Anything different after the fall",
            due_at=utc_now() - timedelta(days=1),
        )
        db.commit()

        result = ProactiveEngine(db).evaluate(person.id)
        db.commit()
        assert result["opportunities"]
        assert result["should_reach_out"] is False
        for opportunity in result["opportunities"]:
            assert opportunity.why_quiet_is_better
            assert opportunity.what_to_say is None

    def test_nothing_is_proposed_when_nothing_is_open(self, db, person):
        result = ProactiveEngine(db).evaluate(person.id)
        db.commit()
        assert result["opportunities"] == []

    def test_a_quiet_record_alone_does_not_justify_contact(self, db, person):
        ProactiveEngine(db).evaluate(person.id, caregiver_local_hour=13)
        db.commit()

    def test_quiet_hours_cover_the_night(self):
        assert _in_quiet_hours(hour=23, now=utc_now()) is True
        assert _in_quiet_hours(hour=3, now=utc_now()) is True
        assert _in_quiet_hours(hour=13, now=utc_now()) is False

    def test_contact_is_suppressed_during_a_crisis(self, db, person):
        """A crisis needs a person, not a notification."""
        BehaviorEngine(db).record_observation(
            person_id=person.id,
            description="Left the house at 2am and was not found for an hour",
            category="elopement",
        )
        db.commit()

        engine = ProactiveEngine(db)
        result = engine.evaluate(person.id, caregiver_local_hour=13)
        db.commit()
        assert result["in_crisis"] is True
        assert result["should_reach_out"] is False

    def test_the_daily_cap_suppresses_further_contact(self, db, person):
        from app.models.follow_up import EngagementOpportunity
        from app.core.care_context_enums import EngagementTrigger

        for _ in range(2):
            db.add(
                EngagementOpportunity(
                    person_id=person.id,
                    trigger=EngagementTrigger.FOLLOW_UP_DUE,
                    should_reach_out=True,
                    is_acted_on=True,
                    acted_at=utc_now(),
                    evidence_ids=[],
                )
            )
        db.commit()

        FollowUpEngine(db).schedule(
            person_id=person.id,
            what_to_recheck="Something overdue",
            due_at=utc_now() - timedelta(days=1),
        )
        db.commit()
        result = ProactiveEngine(db).evaluate(person.id, caregiver_local_hour=13)
        assert result["sent_today"] == 2
        assert result["remaining_today"] == 0
        assert result["should_reach_out"] is False

    def test_a_silence_decision_is_recorded_not_merely_absent(self, db, person):
        note = ProactiveEngine(db).add_silence_note(
            person.id, "Nothing has changed and the caregiver is mid-shift."
        )
        db.commit()
        assert note.should_reach_out is False
        assert "mid-shift" in note.why_quiet_is_better

    def test_a_suppressed_opportunity_cannot_be_sent(self, db, person):
        from app.models.follow_up import EngagementOpportunity
        from app.core.care_context_enums import EngagementTrigger

        row = EngagementOpportunity(
            person_id=person.id,
            trigger=EngagementTrigger.FOLLOW_UP_DUE,
            should_reach_out=False,
            suppressed=True,
            suppression_reason="crisis",
            evidence_ids=[],
        )
        db.add(row)
        db.commit()
        with pytest.raises(ValueError):
            ProactiveEngine(db).mark_acted_on(row.id)

    def test_an_opportunity_that_said_stay_quiet_cannot_be_sent(self, db, person):
        from app.models.follow_up import EngagementOpportunity
        from app.core.care_context_enums import EngagementTrigger

        row = EngagementOpportunity(
            person_id=person.id,
            trigger=EngagementTrigger.FOLLOW_UP_DUE,
            should_reach_out=False,
            evidence_ids=[],
        )
        db.add(row)
        db.commit()
        with pytest.raises(ValueError):
            ProactiveEngine(db).mark_acted_on(row.id)


# ----------------------------------------------------------------------
# Conversation
# ----------------------------------------------------------------------


class TestDriftPolicy:
    def test_a_care_question_stays_in_care_context(self, db, person):
        result = ConversationEngine(db).respond(person.id, "Should I be worried about her sleep?")
        db.commit()
        assert result["stage"] == ConversationStage.IN_CARE_CONTEXT

    def test_small_talk_is_not_dragged_back_to_care(self, db, person):
        result = ConversationEngine(db).respond(person.id, "What's the weather like where you are?")
        db.commit()
        assert result["stage"] == ConversationStage.OUT_OF_CARE_CONTEXT

    def test_venting_is_not_treated_as_a_care_question(self, db, person):
        result = ConversationEngine(db).respond(
            person.id, "I am completely exhausted and I don't know what I'm doing"
        )
        db.commit()
        assert result["stage"] == ConversationStage.DRIFTING

    def test_returning_after_being_away_resumes_rather_than_restarts(
        self, db, person
    ):
        engine = ConversationEngine(db)
        first = engine.respond(person.id, "What about her medication?")
        assert first["session_id"]

        for message in ("What's the weather like?", "And tomorrow?"):
            engine.respond(person.id, message, session_id=first["session_id"])
        db.commit()

        back = engine.respond(
            person.id, "Back to the medication question", session_id=first["session_id"]
        )
        db.commit()
        assert back["stage"] == ConversationStage.RETURNING


class TestConversationSafety:
    def test_emergency_gets_a_referral_regardless_of_anything_else(self, db, person):
        result = ConversationEngine(db).respond(
            person.id, "She can't breathe and I've called an ambulance"
        )
        db.commit()
        assert result["safety_note"]
        assert "emergency services" in result["safety_note"].lower()

    def test_the_caregivers_words_are_stored_verbatim(self, db, person):
        original = "mum WONT take the tablets tonight i am at my wits end"
        result = ConversationEngine(db).respond(person.id, original)
        db.commit()
        from app.models.conversation import ConversationTurn
        from app.core.care_context_enums import SpeakerRole

        caregiver_turn = (
            db.query(ConversationTurn)
            .filter(ConversationTurn.session_id == result["session_id"], ConversationTurn.role == SpeakerRole.CAREGIVER)
            .first()
        )
        assert caregiver_turn.text == original

    def test_a_rejected_reply_falls_back_to_something_honest(self, db, person):
        local, _ = engine_with(
            db,
            guarded_response(
                summary="You should stop the medication tonight.",
                evidence_ids=[],
                possible_context="Whether the prescriber agrees is not established.",
            ),
        )
        result = ConversationEngine(db, local).respond(
            person.id, "Should I skip her evening tablet?"
        )
        db.commit()
        assert "stop the medication" not in result["reply"].lower()
        assert result["reply"]
        assert "could not give you an answer" in result["reply"]

    def test_out_of_context_pulls_no_evidence_in(self, db, person, evidence):
        evidence("e1", "She slept badly.")
        result = ConversationEngine(db).respond(person.id, "Thanks, have a good one")
        db.commit()
        assert result["stage"] == ConversationStage.OUT_OF_CARE_CONTEXT
        assert result["evidence_ids"] == []

    def test_a_session_survives_being_reopened(self, db, person):
        engine = ConversationEngine(db)
        first = engine.respond(person.id, "What about her sleep?")
        second = engine.respond(person.id, "And her appetite?")
        db.commit()
        assert second["session_id"] == first["session_id"]
        view = engine.session_view(first["session_id"])
        assert len(view["turns"]) == 4
        assert view["turn_count"] == 2

    def test_empty_message_is_refused(self, db, person):
        with pytest.raises(ValueError):
            ConversationEngine(db).respond(person.id, "   ")

    def test_missing_session_raises(self, db, person):
        with pytest.raises(ValueError):
            ConversationEngine(db).session_view("nope")
