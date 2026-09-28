from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.care_context_enums import PersonAttributeDomain, ProfileCompleteness
from app.services.care_common import parse_time, utc_now
from app.services.llm import LocalReasoningEngine
from app.services.person_model import PersonModelEngine
from llm_fakes import FakeOllamaClient, guarded_response


def engine_with(db, *responses):
    client = FakeOllamaClient(list(responses))
    engine = LocalReasoningEngine(db=db, client=client)
    engine.enabled = True
    return engine, client


class TestAttributes:
    def test_attribute_is_stored_with_its_evidence(self, db, person, evidence):
        ev = evidence("e1", "She stops eating if the radio is on.")
        model = PersonModelEngine(db)
        attribute = model.add_attribute(
            person_id=person.id,
            domain=PersonAttributeDomain.PREFERENCE,
            label="radio at meals",
            value="Stops eating when it is on",
            evidence_ids=[ev.id],
            source_quote="she won't eat with the radio on",
        )
        db.commit()
        assert attribute.id
        assert model.list_attributes(person.id)[0].value == "Stops eating when it is on"

    def test_confidence_stays_unknown_unless_given(self, db, person):
        model = PersonModelEngine(db)
        attribute = model.add_attribute(
            person_id=person.id, domain=PersonAttributeDomain.COMFORT, label="x", value="y"
        )
        assert attribute.confidence is None
        assert attribute.possible_context is None

    def test_vague_trigger_is_refused(self, db, person):
        model = PersonModelEngine(db)
        with pytest.raises(ValueError):
            model.add_trigger(person_id=person.id, trigger="   ")
        with pytest.raises(ValueError):
            model.add_calmer(person_id=person.id, measure="")

    def test_trigger_records_occurrences_and_calming_measure(self, db, person):
        model = PersonModelEngine(db)
        trigger = model.add_trigger(
            person_id=person.id,
            trigger="Being rushed at bath time",
            response_seen="Becomes very distressed",
            occurrences=6,
            calming_measure="Let her do one thing herself first",
        )
        db.commit()
        assert trigger.occurrences == 6
        assert model.list_triggers(person.id)[0].trigger == "Being rushed at bath time"


class TestCompleteness:
    def test_nothing_recorded_is_thin(self, db, person):
        model = PersonModelEngine(db)
        profile = model.build_profile(person.id)
        db.commit()
        assert profile.completeness == ProfileCompleteness.THIN
        assert profile.summary
        assert "does not yet" in profile.summary
        assert profile.confidence is None

    def test_a_few_attributes_are_partial(self, db, person):
        model = PersonModelEngine(db)
        for index in range(4):
            model.add_attribute(
                person_id=person.id,
                domain=PersonAttributeDomain.PREFERENCE,
                label=f"l{index}",
                value="v",
            )
        profile = model.build_profile(person.id)
        assert profile.completeness == ProfileCompleteness.PARTIAL

    def test_enough_attributes_are_substantial(self, db, person):
        model = PersonModelEngine(db)
        for index in range(9):
            model.add_attribute(
                person_id=person.id,
                domain=PersonAttributeDomain.PREFERENCE,
                label=f"l{index}",
                value="v",
            )
        profile = model.build_profile(person.id)
        assert profile.completeness == ProfileCompleteness.SUBSTANTIAL


class TestProfileSupersession:
    def test_rebuilding_supersedes_rather_than_overwrites(self, db, person):
        model = PersonModelEngine(db)
        first = model.build_profile(person.id)
        model.add_attribute(
            person_id=person.id,
            domain=PersonAttributeDomain.COMFORT,
            label="music",
            value="Stops eating",
        )
        second = model.build_profile(person.id)
        db.commit()

        assert first.is_active is False
        assert first.superseded_by_profile_id == second.id
        assert second.is_active is True
        assert first.summary != ""  # the earlier reading is still readable
        assert model.get_profile(person.id).id == second.id


class TestNarrative:
    def test_model_narrative_is_used_when_available(self, db, person, evidence):
        ev = evidence("e1", "She used to be a schoolteacher.")
        local, _ = engine_with(
            db,
            guarded_response(
                summary="Mum was a schoolteacher and still corrects the news.",
                evidence_ids=[ev.id],
                confidence=0.7,
                possible_context="Whether that is welcome to her now is not recorded.",
            ),
        )
        model = PersonModelEngine(db, local)
        model.add_attribute(
            person_id=person.id,
            domain=PersonAttributeDomain.HISTORY,
            label="work",
            value="Schoolteacher",
            evidence_ids=[ev.id],
        )
        profile = model.build_profile(person.id)
        db.commit()
        assert "schoolteacher" in profile.summary
        assert profile.confidence == 0.7
        assert profile.reasoning_result_id is not None

    def test_deterministic_narrative_survives_no_model(self, db, person):
        model = PersonModelEngine(db)
        for index in range(4):
            model.add_attribute(
                person_id=person.id,
                domain=PersonAttributeDomain.PREFERENCE,
                label=f"l{index}",
                value="v",
            )
        model.add_calmer(person_id=person.id, measure="A walk before breakfast")
        model.add_trigger(person_id=person.id, trigger="The radio before breakfast")
        profile = model.build_profile(person.id)
        db.commit()

        assert "settles them" in profile.summary.lower()
        assert "A walk before breakfast" in profile.summary
        assert "The radio before breakfast" in profile.summary
        assert profile.confidence is None
        assert profile.possible_context

    def test_model_attributes_are_folded_in_only_with_surviving_citations(
        self, db, person, evidence
    ):
        ev = evidence("e1", "She used to sing in the choir.")
        local, _ = engine_with(
            db,
            guarded_response(
                summary="She sang in the choir.",
                evidence_ids=[ev.id, "e-invented"],
                confidence=0.6,
                possible_context="Whether she still sings is not recorded.",
                attributes=[
                    {"label": "choir", "value": "Sang in the choir", "domain": "history"},
                    {"label": "", "value": "should be dropped"},
                ],
            ),
        )
        model = PersonModelEngine(db, local)
        model.add_attribute(
            person_id=person.id,
            domain=PersonAttributeDomain.HISTORY,
            label="seed",
            value="seed",
            evidence_ids=[ev.id],
        )
        model.build_profile(person.id)
        db.commit()

        labels = [a.label for a in model.list_attributes(person.id)]
        assert "choir" in labels
        assert "" not in labels

    def test_describe_is_honest_for_an_unknown_person(self, db, person):
        model = PersonModelEngine(db)
        described = model.describe(person.id)
        assert described["attribute_count"] == 0
        assert described["completeness"] == ProfileCompleteness.THIN
        assert "does not yet establish" in described["known_about_them"]


class TestTimeParsing:
    @pytest.mark.parametrize(
        "value",
        ["2026-01-01T10:00:00Z", "2026-01-01 10:00:00", "2026-01-01", "not a date", None, ""],
    )
    def test_unparsable_times_become_none_not_now(self, value):
        """A missing or broken time must not silently become the current time."""
        parsed = parse_time(value)
        if value in (None, "", "not a date"):
            assert parsed is None
        else:
            assert parsed is not None

    def test_naive_datetimes_are_treated_as_utc(self):
        from datetime import datetime

        parsed = parse_time(datetime(2026, 1, 1, 10, 0))
        assert parsed is not None
        assert parsed.tzinfo is not None

    def test_utc_now_is_timezone_aware(self):
        assert utc_now().tzinfo is not None
        assert (utc_now() + timedelta(days=1)) > utc_now()
