from __future__ import annotations

import pytest

from app.core.care_context_enums import (
    BehaviorCategory,
    ContradictionKind,
    ContradictionResolutionPath,
    DecisionOwner,
    DecisionStatus,
    HypothesisStrength,
)
from app.models.uncertainty import Contradiction as ContradictionModel
from app.services.behavior import BehaviorEngine
from app.services.care_planning import CarePlannerEngine
from app.services.contradiction import ContradictionEngine
from app.services.llm import LocalReasoningEngine
from llm_fakes import FakeOllamaClient, guarded_response


def engine_with(db, *responses):
    client = FakeOllamaClient(list(responses))
    local = LocalReasoningEngine(db=db, client=client)
    local.enabled = True
    return local, client


# ----------------------------------------------------------------------
# Behaviour
# ----------------------------------------------------------------------


class TestObservationIsKeptSeparate:
    def test_the_caregivers_own_words_survive_any_interpretation(self, db, person, caregiver):
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id,
            description="Became very distressed at bath time",
            category=BehaviorCategory.AGITATION,
            original_text="mum absolutely screaming mum wont get in the bath!!",
            observed_by_caregiver_id=caregiver.id,
        )
        db.commit()
        assert observation.original_text == "mum absolutely screaming mum wont get in the bath!!"
        assert observation.description == "Became very distressed at bath time"

    def test_empty_description_is_refused(self, db, person):
        with pytest.raises(ValueError):
            BehaviorEngine(db).record_observation(person_id=person.id, description="  ")

    def test_elopement_is_flagged_as_a_safety_concern_by_default(self, db, person):
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id, description="Left the house", category=BehaviorCategory.ELOPEMENT
        )
        assert observation.is_safety_concern is True

    def test_ordinary_behaviour_is_not_flagged(self, db, person):
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id, description="Wanted a biscuit", category=BehaviorCategory.MOOD
        )
        assert observation.is_safety_concern is False


class TestPluralityIsEnforced:
    def test_one_hypothesis_is_marked_incomplete_not_presented_as_an_answer(
        self, db, person
    ):
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id, description="Refused breakfast", category=BehaviorCategory.REFUSAL
        )
        db.commit()

        local, _ = engine_with(
            db,
            guarded_response(
                summary="She refused breakfast.",
                evidence_ids=[],
                possible_context="Whether this is new is not recorded.",
                hypotheses=[
                    {
                        "explanation": "She was asleep late.",
                        "supporting_evidence_ids": [],
                        "why_plausible": "Plausible but unevidenced.",
                    }
                ],
                what_would_settle_it=[{"check": "Record when she went to bed."}],
            ),
        )
        interpretation = BehaviorEngine(db, local).interpret(observation.id)
        db.commit()

        assert interpretation.hypothesis_count == 1
        assert interpretation.is_complete is False
        assert "not an answer" in interpretation.summary
        assert "should not be acted on" in interpretation.summary
        assert interpretation.requires_human_review is True

    def test_two_hypotheses_are_complete(self, db, person):
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id, description="Refused breakfast", category=BehaviorCategory.REFUSAL
        )
        db.commit()

        local, _ = engine_with(
            db,
            guarded_response(
                summary="Two things are worth ruling in or out.",
                evidence_ids=[],
                possible_context="Neither is established.",
                hypotheses=[
                    {"explanation": "She was asleep late."},
                    {"explanation": "She did not like it."},
                ],
                what_would_settle_it=[{"check": "Record when she went to bed."}],
            ),
        )
        interpretation = BehaviorEngine(db, local).interpret(observation.id)
        db.commit()
        assert interpretation.is_complete is True
        assert interpretation.hypothesis_count == 2

    def test_no_model_still_yields_multiple_open_explanations(self, db, person):
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id, description="Refused breakfast", category=BehaviorCategory.REFUSAL
        )
        db.commit()
        interpretation = BehaviorEngine(db).interpret(observation.id)
        db.commit()
        assert interpretation.hypothesis_count >= 2
        assert "No explanation is established" in interpretation.summary
        assert interpretation.confidence is None


class TestStrengthIsDerivedNotAsserted:
    def test_a_model_cannot_promote_itself_to_supported_on_one_citation(
        self, db, person, evidence
    ):
        ev = evidence("e1", "She was up until 2am.")
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id,
            description="Very drowsy at breakfast",
            category=BehaviorCategory.MOOD,
            evidence_ids=[ev.id],
        )
        db.commit()

        local, _ = engine_with(
            db,
            guarded_response(
                summary="Possibly sleep-deprived.",
                evidence_ids=[ev.id],
                possible_context="Whether that is the whole story is not established.",
                hypotheses=[
                    {
                        "explanation": "She was up until 2am.",
                        "strength": "supported",
                        "supporting_evidence_ids": [ev.id],
                        "why_plausible": "Directly on record.",
                    },
                    {"explanation": "She may not have wanted it.", "strength": "possible"},
                ],
            ),
        )
        interpretation = BehaviorEngine(db, local).interpret(observation.id)
        db.commit()

        hypotheses = interpretation.hypotheses
        top = hypotheses[0]
        assert top["strength"] == HypothesisStrength.POSSIBLE.value
        assert top["claimed_strength"] == "supported"

    def test_two_citations_do_support_it(self, db, person, evidence):
        ev1 = evidence("e1", "She was up until 2am.")
        ev2 = evidence("e2", "She said she had been awake all night.")
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id,
            description="Very drowsy at breakfast",
            category=BehaviorCategory.MOOD,
            evidence_ids=[ev1.id, ev2.id],
        )
        db.commit()

        local, _ = engine_with(
            db,
            guarded_response(
                summary="Sleep-deprived is the best supported reading.",
                evidence_ids=[ev1.id, ev2.id],
                possible_context="Why she was awake is not established.",
                hypotheses=[
                    {
                        "explanation": "She was up all night.",
                        "supporting_evidence_ids": [ev1.id, ev2.id],
                    },
                    {"explanation": "Something else entirely."},
                ],
            ),
        )
        interpretation = BehaviorEngine(db, local).interpret(observation.id)
        db.commit()
        assert interpretation.hypotheses[0]["strength"] == HypothesisStrength.SUPPORTED.value

    def test_fabricated_citations_are_stripped_from_hypotheses(self, db, person, evidence):
        ev = evidence("e1", "She was restless all evening.")
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id,
            description="Agitated at bedtime",
            category=BehaviorCategory.AGITATION,
            evidence_ids=[ev.id],
        )
        db.commit()

        local, _ = engine_with(
            db,
            guarded_response(
                summary="Restlessness is on record.",
                evidence_ids=[ev.id],
                possible_context="What caused it is not established.",
                hypotheses=[
                    {
                        "explanation": "Something seen in the notes.",
                        "supporting_evidence_ids": [ev.id, "e-made-up", "e-also-fake"],
                    },
                    {"explanation": "Something else."},
                ],
            ),
        )
        interpretation = BehaviorEngine(db, local).interpret(observation.id)
        db.commit()
        assert interpretation.hypotheses[0]["supporting_evidence_ids"] == [ev.id]

    def test_ranking_is_by_evidence_not_by_model_order(self, db, person, evidence):
        ev1 = evidence("e1", "She was up all night.")
        ev2 = evidence("e2", "She said she felt unwell.")
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id,
            description="Drowsy and off her food",
            category=BehaviorCategory.MOOD,
            evidence_ids=[ev1.id, ev2.id],
        )
        db.commit()

        local, _ = engine_with(
            db,
            guarded_response(
                summary="Two readings.",
                evidence_ids=[ev1.id, ev2.id],
                possible_context="Neither is established as the cause.",
                hypotheses=[
                    {
                        "explanation": "She felt unwell.",
                        "supporting_evidence_ids": [ev2.id],
                    },
                    {
                        "explanation": "She was up all night.",
                        "supporting_evidence_ids": [ev1.id, ev2.id],
                    },
                ],
            ),
        )
        interpretation = BehaviorEngine(db, local).interpret(observation.id)
        db.commit()
        assert interpretation.hypotheses[0]["explanation"] == "She was up all night."


class TestRejectionIsRemembered:
    def test_a_rejected_hypothesis_is_deactivated_not_deleted(self, db, person, caregiver):
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id, description="Refused lunch", category=BehaviorCategory.REFUSAL
        )
        interpretation = model.interpret(observation.id)
        db.commit()
        hypothesis = model.list_hypotheses(interpretation.id)[0]

        rejected = model.reject_hypothesis(hypothesis.id, "I was there, it was nothing to do with that", caregiver.id)
        db.commit()
        assert rejected.is_active is False
        assert rejected.rejected_reason
        assert rejected.rejected_by_caregiver_id == caregiver.id
        assert rejected.rejected_at is not None


class TestNoModelPath:
    def test_missing_observation_raises(self, db, person):
        with pytest.raises(ValueError):
            BehaviorEngine(db).interpret("does-not-exist")

    def test_new_interpretation_supersedes_the_old(self, db, person):
        model = BehaviorEngine(db)
        observation = model.record_observation(
            person_id=person.id, description="Refused lunch", category=BehaviorCategory.REFUSAL
        )
        db.commit()
        first = model.interpret(observation.id)
        second = model.interpret(observation.id)
        db.commit()
        assert first.is_active is False
        assert first.superseded_by_interpretation_id == second.id


# ----------------------------------------------------------------------
# Contradiction
# ----------------------------------------------------------------------


def add_contradiction(db, person_id="p1", field="sleep", evidence_ids=None):
    row = ContradictionModel(
        person_id=person_id,
        subject_type="sleep",
        subject_id="p1",
        field=field,
        description=f"Reports disagree about {field}",
        evidence_ids=evidence_ids or ["e1", "e2"],
    )
    db.add(row)
    db.commit()
    return row


class TestContradictionAnalysis:
    def test_two_reporters_points_at_observer_difference_not_a_real_conflict(
        self, db, person, evidence
    ):
        from app.models.care import Caregiver, Evidence

        db.add(
            Caregiver(
                id="c2",
                name="Sam",
                email="sam@example.com",
                relationship="son",
                person_id=person.id,
            )
        )
        db.commit()
        evidence("e1", "She slept all night.")
        evidence("e2", "She was up all night.")
        db.query(Evidence).filter(Evidence.id == "e2").first().uploaded_by_caregiver_id = "c2"
        db.commit()

        contradiction = add_contradiction(db)
        analysis = ContradictionEngine(db).analyse(contradiction.id)
        db.commit()

        kinds = [e["kind"] for e in analysis.likely_explanations]
        assert ContradictionKind.OBSERVER_DIFFERENCE.value in kinds
        assert "Do not resolve this by taking the more recent record" in analysis.do_not_resolve_by

    def test_resolvable_explanations_rank_above_a_genuine_conflict(self):
        from app.services.contradiction import _rank_explanations

        ranked = _rank_explanations(
            [
                {"explanation": "genuinely conflict", "kind": "genuine_conflict"},
                {"explanation": "scope", "kind": "scope_mismatch"},
                {"explanation": "supersession", "kind": "supersession"},
            ],
            [],
        )
        assert ranked[0]["kind"] == "scope_mismatch"
        assert ranked[-1]["kind"] == "genuine_conflict"

    def test_analysis_never_resolves_anything(self, db, person, evidence):
        evidence("e1", "One account.")
        evidence("e2", "Another account.")
        contradiction = add_contradiction(db)
        ContradictionEngine(db).analyse(contradiction.id)
        db.commit()
        stored = db.query(ContradictionModel).filter(ContradictionModel.id == contradiction.id).first()
        assert stored.resolution_status == "unresolved"
        assert stored.resolved_at is None

    def test_a_scope_mismatch_is_marked_auto_resolvable(self, db, person, evidence):
        from app.models.care import CareEvent
        from app.services.care_common import utc_now

        evidence("e1", "One account.")
        evidence("e2", "Another account.")
        db.add(
            CareEvent(
                person_id=person.id,
                event_type="sleep",
                title="Slept well",
                occurred_at=utc_now() - __import__("datetime").timedelta(days=1),
                recorded_at=utc_now() - __import__("datetime").timedelta(days=1),
                created_by_caregiver_id="c1",
                location="Home",
            )
        )
        db.add(
            CareEvent(
                person_id=person.id,
                event_type="sleep",
                title="Slept badly",
                occurred_at=utc_now() - __import__("datetime").timedelta(days=1),
                recorded_at=utc_now(),
                created_by_caregiver_id="c1",
                location="Hospital",
            )
        )
        db.commit()
        contradiction = add_contradiction(db)
        analysis = ContradictionEngine(db).analyse(contradiction.id)
        db.commit()
        if analysis.primary_kind == ContradictionKind.SCOPE_MISMATCH:
            assert analysis.is_auto_resolvable is True
            assert analysis.recommended_path == ContradictionResolutionPath.ACCEPT_BOTH

    def test_missing_contradiction_raises(self, db, person):
        with pytest.raises(ValueError):
            ContradictionEngine(db).analyse("nope")

    def test_closing_requires_a_person_and_records_the_reason(self, db, person, evidence):
        evidence("e1", "One account.")
        evidence("e2", "Another account.")
        contradiction = add_contradiction(db)
        model = ContradictionEngine(db)
        analysis = model.analyse(contradiction.id)
        resolution = model.record_resolution(
            analysis.id,
            ContradictionResolutionPath.ASK_OBSERVER,
            "Spoke to Jo, who was there. She was up twice.",
            reason="Jo was present and remembers clearly",
            caregiver_id="c1",
        )
        db.commit()
        assert resolution.id
        assert resolution.path_taken == ContradictionResolutionPath.ASK_OBSERVER
        assert ContradictionEngine(db).list_resolutions(contradiction.id)

    def test_open_summary_flags_unanalysed_contradictions(self, db, person, evidence):
        evidence("e1", "One account.")
        evidence("e2", "Another account.")
        add_contradiction(db)
        summary = ContradictionEngine(db).unresolved_summary(person.id)
        assert summary["open_count"] == 1
        assert summary["unanalysed_count"] == 1

    def test_empty_outcome_is_refused(self, db, person, evidence):
        evidence("e1", "One account.")
        evidence("e2", "Another account.")
        contradiction = add_contradiction(db)
        analysis = ContradictionEngine(db).analyse(contradiction.id)
        with pytest.raises(ValueError):
            ContradictionEngine(db).record_resolution(analysis.id, "ask_observer", "   ")


# ----------------------------------------------------------------------
# Care planning
# ----------------------------------------------------------------------


class TestDecisions:
    def test_monitoring_is_always_an_option(self, db, person):
        decision = CarePlannerEngine(db).propose_decision(
            person_id=person.id, question="Should we move the bedtime earlier?"
        )
        db.commit()
        options = " ".join(str(o["option"]).lower() for o in decision.options)
        assert "watch" in options or "nothing" in options
        assert decision.option_count >= 2

    def test_a_clinical_decision_belongs_to_a_clinician(self, db, person):
        decision = CarePlannerEngine(db).propose_decision(
            person_id=person.id, question="Should we increase the donepezil dose?"
        )
        db.commit()
        assert decision.owner == DecisionOwner.CLINICIAN
        assert decision.requires_clinician is True
        assert decision.safety_decision == "clinical_decision_not_made_here"

    def test_a_model_cannot_take_a_clinical_decision_over(self, db, person):
        """Even a confident model must not claim a clinician's decision."""
        local, _ = engine_with(
            db,
            guarded_response(
                summary="Increase the dose.",
                evidence_ids=[],
                possible_context="Whether the prescriber agrees is not established.",
                decision_owner="caregiver",
                options=[{"option": "Increase the dose"}, {"option": "Leave it as it is"}],
            ),
        )
        decision = CarePlannerEngine(db, local).propose_decision(
            person_id=person.id, question="Should we increase the donepezil dose?"
        )
        db.commit()
        assert decision.owner == DecisionOwner.CLINICIAN

    def test_nothing_is_ever_accepted_automatically(self, db, person):
        decision = CarePlannerEngine(db).propose_decision(
            person_id=person.id, question="Should we try a day centre?"
        )
        db.commit()
        assert decision.status == DecisionStatus.PROPOSED
        assert decision.requires_human_review is True
        assert decision.decided_at is None

    def test_a_person_can_accept_or_reject(self, db, person):
        model = CarePlannerEngine(db)
        decision = model.propose_decision(person_id=person.id, question="Try the day centre?")
        db.commit()
        accepted = model.decide(decision.id, True, "She enjoyed the visit", "c1")
        db.commit()
        assert accepted.status == DecisionStatus.ACCEPTED
        assert accepted.decided_by_caregiver_id == "c1"
        with pytest.raises(ValueError):
            model.decide(decision.id, True)

    def test_empty_question_is_refused(self, db, person):
        with pytest.raises(ValueError):
            CarePlannerEngine(db).propose_decision(person_id=person.id, question="  ")


class TestPlans:
    def test_plan_steps_carry_an_owner_and_a_success_signal(self, db, person):
        plan = CarePlannerEngine(db).build_plan(person_id=person.id, title="Try the day centre")
        db.commit()
        assert plan.step_count >= 1
        for step in plan.steps:
            assert step.get("owner")
            assert step.get("how_we_know_it_worked")

    def test_plan_notes_when_an_action_does_not_serve_the_person(self, db, person):
        from app.services.what_matters import WhatMattersEngine

        WhatMattersEngine(db).add_entry(
            person_id=person.id,
            statement="She does not want to go anywhere new",
            importance="defining",
        )
        db.commit()

        local, _ = engine_with(
            db,
            # 1. the decision itself
            guarded_response(
                summary="A new placement is being considered.",
                evidence_ids=[],
                possible_context="Whether her view has changed is not established.",
                options=[
                    {"option": "Arrange a new placement"},
                    {"option": "Do nothing for now, and watch closely"},
                ],
            ),
            # 2. the plan built from it
            guarded_response(
                summary="Visit first, without committing.",
                evidence_ids=[],
                possible_context="Whether she would enjoy it is not established.",
                steps=[
                    {
                        "step": "Visit the centre with her once.",
                        "owner": "caregiver",
                        "when": "this week",
                        "how_we_know_it_worked": "She comes back willing to go again.",
                    }
                ],
            ),
            # 3. the alignment check, run as the plan is written
            guarded_response(
                summary="This goes against something she has said clearly.",
                evidence_ids=[],
                possible_context="Whether her view has changed is not established.",
                verdict="conflicts",
                reasoning="She has said she does not want to go anywhere new.",
            ),
        )
        planner = CarePlannerEngine(db, local)
        decision = planner.propose_decision(
            person_id=person.id, question="Arrange a new placement"
        )
        db.commit()
        plan = planner.build_plan(person_id=person.id, decision_id=decision.id)
        db.commit()
        assert "does not clearly serve" in plan.summary

    def test_plan_has_a_review_date_and_escalation_signs(self, db, person):
        plan = CarePlannerEngine(db).build_plan(person_id=person.id, title="Something")
        db.commit()
        assert plan.review_at is not None
        assert plan.stop_and_escalate_if

    def test_completing_a_step_records_when_it_did_not_work(self, db, person):
        model = CarePlannerEngine(db)
        plan = model.build_plan(person_id=person.id, title="Something")
        db.commit()
        from app.models.care_planning import CarePlanStep

        step = CarePlanStep(
            plan_id=plan.id, person_id=person.id, sequence=99, step="Try it", owner="caregiver"
        )
        db.add(step)
        db.commit()
        done = model.complete_step(step.id, worked=False)
        db.commit()
        assert done.is_completed is True
        assert "not working" in done.how_we_know_it_worked

    def test_progress_is_counted(self, db, person):
        model = CarePlannerEngine(db)
        plan = model.build_plan(person_id=person.id, title="Something")
        db.commit()
        progress = model.plan_progress(plan.id)
        assert progress["total"] == plan.step_count
        assert progress["completed"] == 0
        with pytest.raises(ValueError):
            model.plan_progress("nope")
