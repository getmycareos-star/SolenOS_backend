from __future__ import annotations

import pytest

from app.core.care_context_enums import (
    AlignmentVerdict,
    MattersCategory,
    MattersImportance,
    TrajectoryDirection,
)
from app.services.llm import LocalReasoningEngine
from app.services.trajectory import TrajectoryEngine
from app.services.what_matters import WhatMattersEngine
from llm_fakes import FakeOllamaClient, guarded_response


def engine_with(db, *responses):
    client = FakeOllamaClient(list(responses))
    local = LocalReasoningEngine(db=db, client=client)
    local.enabled = True
    return local, client


# ----------------------------------------------------------------------
# What matters
# ----------------------------------------------------------------------


class TestWhatMattersEntries:
    def test_entry_records_whether_the_person_voiced_it(self, db, person):
        model = WhatMattersEngine(db)
        voiced = model.add_entry(
            person_id=person.id,
            statement="She wants to keep going to the gardening club",
            category=MattersCategory.PREFERENCE,
            importance=MattersImportance.HIGH,
        )
        inferred = model.add_entry(
            person_id=person.id,
            statement="The family value independence",
            category=MattersCategory.VALUE,
        )
        db.commit()
        assert voiced.attributed_to_person is True
        assert inferred.attributed_to_person is False

    def test_empty_statement_is_refused(self, db, person):
        with pytest.raises(ValueError):
            WhatMattersEngine(db).add_entry(person_id=person.id, statement="  ")

    def test_filtering_by_importance(self, db, person):
        model = WhatMattersEngine(db)
        model.add_entry(person_id=person.id, statement="low thing", importance=MattersImportance.LOW)
        model.add_entry(
            person_id=person.id, statement="big thing", importance=MattersImportance.DEFINING
        )
        db.commit()
        high = model.list_entries(person.id, minimum_importance=MattersImportance.HIGH)
        assert [e.statement for e in high] == ["big thing"]

    def test_view_reports_its_own_gaps(self, db, person):
        model = WhatMattersEngine(db)
        model.add_entry(person_id=person.id, statement="She likes the garden")
        view = model.what_matters_view(person.id)
        assert view["entry_count"] == 1
        assert any("someone else's account" in gap for gap in view["known_gaps"])
        assert any("No tensions" in gap for gap in view["known_gaps"])


class TestExtractionRefusesClinicalFacts:
    def test_symptoms_are_not_recorded_as_values(self, db, person, caregiver):
        from app.models.care import CareEvent
        from app.services.care_common import utc_now

        db.add(
            CareEvent(
                person_id=person.id,
                event_type="mobility",
                title="She has difficulty walking to the bathroom",
                occurred_at=utc_now(),
                recorded_at=utc_now(),
                created_by_caregiver_id=caregiver.id,
            )
        )
        db.commit()

        local, _ = engine_with(
            db,
            guarded_response(
                summary="One value was found.",
                evidence_ids=[],
                possible_context="Whether this reflects her current wishes is not established.",
                entries=[
                    {
                        "statement": "She has difficulty walking",
                        "how_much_it_matters": "high",
                        "category": "value",
                    },
                    {
                        "statement": "She wants to keep going to the gardening club",
                        "how_much_it_matters": "high",
                        "category": "preference",
                    },
                ],
            ),
        )
        created = WhatMattersEngine(db, local).extract(person.id)
        db.commit()
        statements = [e.statement for e in created]
        assert "She wants to keep going to the gardening club" in statements
        assert "She has difficulty walking" not in statements

    def test_duplicates_are_not_added_twice(self, db, person, caregiver):
        from app.models.care import CareEvent
        from app.services.care_common import utc_now

        db.add(
            CareEvent(
                person_id=person.id,
                event_type="preference",
                title="Gardening club",
                occurred_at=utc_now(),
                recorded_at=utc_now(),
                created_by_caregiver_id=caregiver.id,
            )
        )
        db.commit()
        model = WhatMattersEngine(db)
        model.add_entry(person_id=person.id, statement="She wants the gardening club")

        local, _ = engine_with(
            db,
            guarded_response(
                summary="x",
                evidence_ids=[],
                possible_context="Not established whether this is current.",
                entries=[
                    {"statement": "She wants the gardening club", "how_much_it_matters": "high"},
                ],
            ),
        )
        assert WhatMattersEngine(db, local).extract(person.id) == []

    def test_extract_returns_nothing_without_a_model(self, db, person, caregiver):
        from app.models.care import CareEvent
        from app.services.care_common import utc_now

        db.add(
            CareEvent(
                person_id=person.id,
                event_type="preference",
                title="Gardening club",
                occurred_at=utc_now(),
                recorded_at=utc_now(),
                created_by_caregiver_id=caregiver.id,
            )
        )
        db.commit()
        assert WhatMattersEngine(db).extract(person.id) == []


class TestAlignment:
    def test_no_entries_means_unknown_not_a_verdict(self, db, person):
        assessment = WhatMattersEngine(db).assess_action(
            person_id=person.id, action_description="Move her into a day centre"
        )
        db.commit()
        assert assessment.verdict == AlignmentVerdict.UNKNOWN
        assert assessment.confidence is None
        assert "does not establish" in assessment.reasoning or assessment.possible_context

    def test_empty_action_is_refused(self, db, person):
        with pytest.raises(ValueError):
            WhatMattersEngine(db).assess_action(person_id=person.id, action_description="  ")

    def test_conflict_is_reported_when_the_action_contradicts_a_value(self, db, person, evidence):
        ev = evidence("e1", "She says she does not want to go anywhere new.")
        model = WhatMattersEngine(db)
        model.add_entry(
            person_id=person.id,
            statement="She does not want to go anywhere new",
            category=MattersCategory.PREFERENCE,
            importance=MattersImportance.DEFINING,
            what_it_rules_out=["a new placement"],
            evidence_ids=[ev.id],
        )
        db.commit()

        local, _ = engine_with(
            db,
            guarded_response(
                summary="This goes against something she has said clearly.",
                evidence_ids=[ev.id],
                confidence=0.6,
                possible_context="Whether her view has changed is not recorded.",
                verdict="conflicts",
                tension="Her stated preference against the placement.",
                suggested_adaptation="Visit the centre first, without committing.",
            ),
        )
        assessment = WhatMattersEngine(db, local).assess_action(
            person_id=person.id, action_description="Arrange a new placement"
        )
        db.commit()
        assert assessment.verdict == AlignmentVerdict.CONFLICTS
        assert assessment.suggested_adaptation is not None
        assert assessment.entry_ids

    def test_keyword_fallback_is_honest_about_being_weak(self, db, person):
        model = WhatMattersEngine(db)
        model.add_entry(
            person_id=person.id, statement="She values the gardening club every week"
        )
        db.commit()
        assessment = model.assess_action(
            person_id=person.id, action_description="Cancel the gardening club place"
        )
        db.commit()
        assert assessment.verdict in (
            AlignmentVerdict.PARTLY_SERVES,
            AlignmentVerdict.UNKNOWN,
        )
        assert "keyword match" in (assessment.reasoning or "") or "cannot answer" in (
            assessment.reasoning or ""
        )


# ----------------------------------------------------------------------
# Trajectory
# ----------------------------------------------------------------------


class TestTrajectory:
    def test_qualitative_only_points_do_not_make_a_trajectory(self, db, person):
        model = TrajectoryEngine(db)
        for index in range(5):
            model.record_point(person_id=person.id, subject="mood", value_text="settled")
        with pytest.raises(ValueError):
            model.track(person.id, "mood")

    def test_two_points_is_not_a_trend(self, db, person):
        model = TrajectoryEngine(db)
        model.record_point(person_id=person.id, subject="weight", value=80)
        model.record_point(person_id=person.id, subject="weight", value=78)
        trajectory = model.track(person.id, "weight")
        db.commit()
        assert trajectory.direction == TrajectoryDirection.INSUFFICIENT_DATA
        assert trajectory.point_count == 2
        assert trajectory.confidence is None
        assert "not enough" in trajectory.plain_reading

    def test_clear_decline_is_detected(self, db, person):
        model = TrajectoryEngine(db)
        for index, value in enumerate([80, 79, 78.5, 77, 75, 73]):
            model.record_point(
                person_id=person.id,
                subject="weight",
                value=value,
                observed_at=f"2026-01-{index + 1:02d}",
            )
        trajectory = model.track(person.id, "weight")
        db.commit()
        assert trajectory.direction == TrajectoryDirection.DECLINING
        assert trajectory.slope_per_week is not None
        assert trajectory.point_count == 6

    def test_clear_improvement_is_detected(self, db, person):
        model = TrajectoryEngine(db)
        for index, value in enumerate([70, 71, 72, 73, 74.5, 76]):
            model.record_point(
                person_id=person.id,
                subject="weight",
                value=value,
                observed_at=f"2026-02-{index + 1:02d}",
            )
        trajectory = model.track(person.id, "weight")
        db.commit()
        assert trajectory.direction == TrajectoryDirection.IMPROVING

    def test_movement_inside_baseline_variation_is_not_a_decline(self, db, person):
        """The single most important thing this engine does.

        A person whose weight has always moved by a kilo or two has not declined
        when it moves by a kilo or two.
        """
        model = TrajectoryEngine(db)
        for index, value in enumerate([80, 81, 79.5, 80.5, 79, 80.2]):
            model.record_point(
                person_id=person.id,
                subject="weight",
                value=value,
                observed_at=f"2026-03-{index + 1:02d}",
            )
        trajectory = model.track(person.id, "weight", baseline_variation=1.5)
        db.commit()
        assert trajectory.baseline_variation_explains is True
        assert trajectory.direction == TrajectoryDirection.STABLE
        assert "normal variation" in trajectory.plain_reading

    def test_same_data_without_baseline_is_flagged_not_hidden(self, db, person):
        model = TrajectoryEngine(db)
        for index, value in enumerate([80, 79, 77, 74, 71, 68]):
            model.record_point(
                person_id=person.id,
                subject="weight",
                value=value,
                observed_at=f"2026-04-{index + 1:02d}",
            )
        trajectory = model.track(person.id, "weight")
        db.commit()
        assert trajectory.direction == TrajectoryDirection.DECLINING
        assert trajectory.baseline_variation_explains is None

    def test_slope_uses_elapsed_time_not_reading_order(self, db, person):
        from datetime import timedelta

        from app.services.care_common import utc_now

        model = TrajectoryEngine(db)
        start = utc_now() - timedelta(days=84)
        for index, value in enumerate([80, 76, 72, 68]):
            model.record_point(
                person_id=person.id,
                subject="weight",
                value=value,
                observed_at=start + timedelta(weeks=index),
            )
        trajectory = model.track(person.id, "weight")
        db.commit()
        # Four points a week apart, losing 4 per week.
        assert trajectory.slope_per_week == pytest.approx(-4.0, abs=0.2)

    def test_rising_pain_is_a_decline_when_higher_is_worse(self, db, person):
        """Weight rising and pain rising are opposite events.

        The engine must not assume which one it is looking at.
        """
        model = TrajectoryEngine(db)
        for index, value in enumerate([3, 4, 5, 6, 7, 8]):
            model.record_point(
                person_id=person.id,
                subject="pain_score",
                value=value,
                observed_at=f"2026-07-{index + 1:02d}",
            )
        trajectory = model.track(person.id, "pain_score", higher_is_worse=True)
        db.commit()
        assert trajectory.direction == TrajectoryDirection.DECLINING
        assert trajectory.higher_is_worse is True

    def test_falling_values_are_worse_under_the_default(self, db, person):
        """The default is "lower is worse", which is right for weight and appetite.

        It is wrong for pain, which is why higher_is_worse exists.
        """
        model = TrajectoryEngine(db)
        for index, value in enumerate([8, 7, 6, 5, 4, 3]):
            model.record_point(
                person_id=person.id,
                subject="pain_score",
                value=value,
                observed_at=f"2026-08-{index + 1:02d}",
            )
        trajectory = model.track(person.id, "pain_score")
        db.commit()
        assert trajectory.direction == TrajectoryDirection.DECLINING
        assert trajectory.higher_is_worse is False

    def test_falling_pain_is_improving_when_higher_is_worse_is_set(self, db, person):
        model = TrajectoryEngine(db)
        for index, value in enumerate([8, 7, 6, 5, 4, 3]):
            model.record_point(
                person_id=person.id,
                subject="pain_score",
                value=value,
                observed_at=f"2026-09-{index + 1:02d}",
            )
        trajectory = model.track(person.id, "pain_score", higher_is_worse=True)
        db.commit()
        assert trajectory.direction == TrajectoryDirection.IMPROVING

    def test_model_cannot_overturn_the_arithmetic(self, db, person):
        model = TrajectoryEngine(db)
        for index, value in enumerate([80, 79, 78.5, 77, 75, 73]):
            model.record_point(
                person_id=person.id,
                subject="weight",
                value=value,
                observed_at=f"2026-05-{index + 1:02d}",
            )
        db.commit()

        local, _ = engine_with(
            db,
            guarded_response(
                summary="Things look fine and she is improving.",
                evidence_ids=[],
                confidence=0.9,
                possible_context="Whether the readings are comparable is not established.",
                plain_reading="Things look fine and she is improving.",
            ),
        )
        trajectory = TrajectoryEngine(db, local).track(person.id, "weight")
        db.commit()
        assert trajectory.direction == TrajectoryDirection.DECLINING
        assert "improving" not in trajectory.plain_reading.lower()

    def test_retraacking_supersedes_rather_than_overwrites(self, db, person):
        model = TrajectoryEngine(db)
        for index, value in enumerate([80, 78, 75, 73]):
            model.record_point(
                person_id=person.id,
                subject="weight",
                value=value,
                observed_at=f"2026-06-{index + 1:02d}",
            )
        first = model.track(person.id, "weight")
        second = model.track(person.id, "weight")
        db.commit()
        assert first.is_active is False
        assert first.superseded_by_trajectory_id == second.id
