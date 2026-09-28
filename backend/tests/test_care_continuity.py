from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.care_context_enums import HandoffReason
from app.models.care import CareEvent
from app.services.care_common import utc_now
from app.services.care_continuity import CareContinuityEngine


def add_event(
    db,
    person_id="p1",
    caregiver_id="c1",
    title="Woke at 3am",
    event_type="sleep",
    days_ago=0,
    location=None,
    occurred=True,
):
    recorded = utc_now() - timedelta(days=days_ago)
    row = CareEvent(
        person_id=person_id,
        event_type=event_type,
        title=title,
        description=title,
        occurred_at=recorded if occurred else None,
        recorded_at=recorded,
        created_by_caregiver_id=caregiver_id,
        location=location,
    )
    db.add(row)
    db.commit()
    return row


class TestThreads:
    def test_first_touch_creates_a_thread(self, db, person, caregiver):
        engine = CareContinuityEngine(db)
        thread = engine.touch_thread(person.id, "Sleep")
        db.commit()
        assert thread.id
        assert thread.subject == "sleep"
        assert thread.observation_count == 1
        assert thread.is_stale is False

    def test_subject_is_normalised(self, db, person):
        engine = CareContinuityEngine(db)
        assert engine.touch_thread(person.id, "  SLEEP ").subject == "sleep"

    def test_subjectless_thread_is_refused(self, db, person):
        with pytest.raises(ValueError):
            CareContinuityEngine(db).touch_thread(person.id, "  ")

    def test_repeat_touches_do_not_create_duplicates(self, db, person):
        engine = CareContinuityEngine(db)
        engine.touch_thread(person.id, "sleep")
        engine.touch_thread(person.id, "sleep")
        engine.touch_thread(person.id, "sleep")
        db.commit()
        assert len(engine.list_threads(person.id)) == 1
        assert engine.get_thread(person.id, "sleep").observation_count == 3

    def test_window_spans_first_to_last(self, db, person):
        engine = CareContinuityEngine(db)
        engine.touch_thread(person.id, "sleep", observed_at=utc_now() - timedelta(days=20))
        thread = engine.touch_thread(person.id, "sleep", observed_at=utc_now())
        db.commit()
        assert thread.first_recorded_at < thread.last_recorded_at


class TestStaleness:
    def test_quiet_thread_is_marked_stale_with_a_date(self, db, person):
        engine = CareContinuityEngine(db)
        thread = engine.touch_thread(
            person.id, "sleep", observed_at=utc_now() - timedelta(days=20)
        )
        changed = engine.refresh_staleness(person.id)
        db.commit()
        assert thread.is_stale is True
        assert thread.stale_since is not None
        assert len(changed) == 1

    def test_current_thread_is_not_stale(self, db, person):
        engine = CareContinuityEngine(db)
        engine.touch_thread(person.id, "sleep", observed_at=utc_now())
        engine.refresh_staleness(person.id)
        db.commit()
        assert engine.get_thread(person.id, "sleep").is_stale is False

    def test_new_activity_clears_staleness(self, db, person):
        engine = CareContinuityEngine(db)
        engine.touch_thread(person.id, "sleep", observed_at=utc_now() - timedelta(days=20))
        engine.refresh_staleness(person.id)
        thread = engine.touch_thread(person.id, "sleep", observed_at=utc_now())
        db.commit()
        assert thread.is_stale is False
        assert thread.stale_since is None


class TestSnapshot:
    def test_snapshot_has_three_parts_and_reports_gaps(self, db, person, caregiver):
        for index in range(6):
            add_event(db, title=f"Night {index}", days_ago=30 - index * 5)
        engine = CareContinuityEngine(db)
        engine.touch_thread(person.id, "sleep", observed_at=utc_now() - timedelta(days=30))
        snapshot = engine.build_snapshot(person.id, "sleep")
        db.commit()

        assert snapshot.previously_known is not None
        assert snapshot.currently_known is not None
        assert snapshot.what_will_matter_next is not None
        assert snapshot.gaps
        assert "days" in snapshot.gaps[0]["description"]

    def test_undated_events_are_never_treated_as_recent(self, db, person, caregiver):
        add_event(db, title="Undated thing", occurred=False)
        engine = CareContinuityEngine(db)
        engine.touch_thread(person.id, "sleep")
        snapshot = engine.build_snapshot(person.id, "sleep")
        db.commit()
        assert any("no time attached" in str(g) or "cannot be placed" in str(g) for g in snapshot.gaps)

    def test_empty_record_says_so(self, db, person):
        engine = CareContinuityEngine(db)
        snapshot = engine.build_snapshot(person.id, "nothing_recorded")
        db.commit()
        assert snapshot.gaps
        assert snapshot.confidence is None
        assert snapshot.possible_context

    def test_snapshot_supersedes_rather_than_overwrites(self, db, person, caregiver):
        add_event(db)
        engine = CareContinuityEngine(db)
        engine.touch_thread(person.id, "sleep")
        first = engine.build_snapshot(person.id, "sleep")
        second = engine.build_snapshot(person.id, "sleep")
        db.commit()
        assert first.is_active is False
        assert first.superseded_by_snapshot_id == second.id


class TestChangeDetection:
    def test_caregiver_changes_are_counted(self, db, person):
        from app.models.care import Caregiver

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
        add_event(db, caregiver_id="c1", days_ago=6)
        add_event(db, caregiver_id="c1", days_ago=5)
        add_event(db, caregiver_id="c2", days_ago=4)
        add_event(db, caregiver_id="c2", days_ago=3)
        add_event(db, caregiver_id="c1", days_ago=2)

        assert CareContinuityEngine(db).count_caregiver_changes(person.id) == 2

    def test_setting_changes_are_counted(self, db, person):
        add_event(db, location="Home", days_ago=4)
        add_event(db, location="Home", days_ago=3)
        add_event(db, location="Hospital", days_ago=2)
        add_event(db, location="Home", days_ago=1)
        assert CareContinuityEngine(db).count_setting_changes(person.id) == 2

    def test_no_changes_when_one_caregiver_all_the_way(self, db, person):
        for index in range(3):
            add_event(db, caregiver_id="c1", days_ago=index)
        assert CareContinuityEngine(db).count_caregiver_changes(person.id) == 0


class TestHandoff:
    def test_handoff_names_the_gap_rather_than_smoothing_it(self, db, person, caregiver):
        add_event(db, days_ago=30)
        add_event(db, days_ago=2)
        engine = CareContinuityEngine(db)
        engine.touch_thread(person.id, "sleep", observed_at=utc_now() - timedelta(days=30))
        handoff = engine.build_handoff(person.id, HandoffReason.CAREGIVER_CHANGE, "c1", "c2")
        db.commit()

        assert handoff.gap_days is not None and handoff.gap_days >= 20
        assert handoff.evidence_ids is not None
        assert handoff.possible_context
        assert handoff.is_acknowledged is False

    def test_handoff_carries_known_practices(self, db, person):
        from app.services.person_model import PersonModelEngine

        model = PersonModelEngine(db)
        model.add_trigger(
            person_id=person.id,
            trigger="Being rushed at bath time",
            response_seen="Distressed",
            calming_measure="Let her start herself",
        )
        model.add_calmer(person_id=person.id, measure="A walk before breakfast")
        handoff = CareContinuityEngine(db).build_handoff(person.id)
        db.commit()

        assert handoff.what_goes_wrong
        assert handoff.what_goes_wrong[0]["situation"] == "Being rushed at bath time"
        assert "A walk before breakfast" in " ".join(handoff.first_24_hours or [])

    def test_a_handoff_keeps_the_summary_the_model_wrote(self, db, person):
        """The narrative a model produced must not vanish on the model path."""
        from llm_fakes import FakeOllamaClient, guarded_response
        from app.services.llm import LocalReasoningEngine

        client = FakeOllamaClient(
            [
                guarded_response(
                    summary="She has been unsettled since the room changed.",
                    evidence_ids=[],
                    possible_context="Whether it is the room is not established.",
                )
            ]
        )
        local = LocalReasoningEngine(db=db, client=client)
        local.enabled = True

        handoff = CareContinuityEngine(db, local).build_handoff(person.id)
        db.commit()
        assert handoff.summary == "She has been unsettled since the room changed."

    def test_first_24_hours_names_the_ask_when_nothing_is_known(self, db, person):
        handoff = CareContinuityEngine(db).build_handoff(person.id)
        db.commit()
        assert any("outgoing caregiver" in item for item in handoff.first_24_hours)

    def test_stale_threads_are_surfaced_for_the_new_caregiver(self, db, person):
        engine = CareContinuityEngine(db)
        engine.touch_thread(person.id, "sleep", observed_at=utc_now() - timedelta(days=30))
        handoff = engine.build_handoff(person.id)
        db.commit()
        assert handoff.open_threads
        assert "days" in handoff.open_threads[0]


class TestBrief:
    def test_brief_leads_with_the_one_line_that_matters(self, db, person):
        engine = CareContinuityEngine(db)
        engine.touch_thread(person.id, "sleep", observed_at=utc_now() - timedelta(days=30))
        brief = engine.continuity_brief(person.id)
        db.commit()
        assert "Sleep" in brief["the_one_line_that_matters"]
        assert "gap in the record" in brief["the_one_line_that_matters"]

    def test_empty_record_says_nothing_is_recorded(self, db, person):
        brief = CareContinuityEngine(db).continuity_brief(person.id)
        assert "Nothing has been recorded" in brief["the_one_line_that_matters"]
        assert brief["stale_threads"] == []
