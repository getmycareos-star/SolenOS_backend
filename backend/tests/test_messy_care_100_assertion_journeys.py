"""100 independently labelled PostgreSQL care-assertion journeys.

Each scenario writes two conflicting, separately attributed reports through the real
care-continuity service, commits them, opens a new database session, and verifies
that both original assertions and the unresolved contradiction survive retrieval.
These are synthetic engineering fixtures, not clinical validation.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app  # noqa: F401 - registers all ORM models and revision listeners
from app.models.care import Caregiver, Evidence, Person
from app.models.care_continuity import CareAssertion, CareStateRevision
from app.models.uncertainty import Contradiction
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion

DATABASE_URL = os.getenv("SOLENOS_BENCHMARK_DATABASE_URL", "sqlite:///:memory:")
OPTIONS = {}
if DATABASE_URL.startswith("sqlite"):
    OPTIONS["connect_args"] = {"check_same_thread": False}
    if DATABASE_URL == "sqlite:///:memory:":
        OPTIONS["poolclass"] = StaticPool
engine = create_engine(DATABASE_URL, **OPTIONS)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

FIELDS = (
    "medication_administration", "mobility_support", "meal_intake",
    "night_waking", "pain_report", "appointment_attendance",
    "fluid_intake", "location", "personal_care", "mood_observation",
)
PAIRS_BY_FIELD = {
    "medication_administration": (
        ("given", "not_given"), ("administered", "withheld"),
        ("morning_dose_taken", "morning_dose_missed"), ("evening_dose_taken", "evening_dose_missed"),
        ("dose_confirmed", "dose_unconfirmed"), ("pill_swallowed", "pill_found_unconsumed"),
        ("prescribed_schedule_followed", "schedule_not_followed"), ("dose_recorded", "no_record_of_dose"),
        ("caregiver_observed_administration", "caregiver_did_not_observe_administration"),
        ("pharmacy_pack_empty", "pharmacy_pack_dose_present"),
    ),
    "mobility_support": (
        ("walked_independently", "needed_hands_on_support"), ("transferred_with_one_helper", "needed_two_helpers"),
        ("used_walker", "could_not_use_walker"), ("stood_without_help", "needed_help_to_stand"),
        ("walked_to_bathroom", "could_not_walk_to_bathroom"), ("steady_gait_reported", "unsteady_gait_reported"),
        ("used_stairs", "did_not_use_stairs"), ("self_transferred", "transfer_assisted"),
        ("walked_outdoors", "remained_indoors"), ("usual_support_needed", "increased_support_needed"),
    ),
    "meal_intake": (
        ("ate_full_meal", "ate_few_bites"), ("finished_breakfast", "skipped_breakfast"),
        ("ate_lunch", "refused_lunch"), ("ate_dinner", "refused_dinner"),
        ("normal_appetite_reported", "reduced_appetite_reported"), ("drank_supplement", "declined_supplement"),
        ("ate_without_prompting", "needed_repeated_prompting"), ("swallowed_meal", "meal_left_uneaten"),
        ("ate_solid_food", "only_took_liquids"), ("meal_tolerance_good", "meal_tolerance_poor"),
    ),
    "night_waking": (
        ("slept_through", "woke_repeatedly"), ("one_waking", "multiple_wakings"),
        ("returned_to_sleep", "could_not_return_to_sleep"), ("no_night_wandering", "night_wandering_reported"),
        ("usual_sleep_duration", "shortened_sleep_duration"), ("settled_without_help", "needed_reassurance"),
        ("no_overnight_call", "overnight_call_for_help"), ("quiet_night", "disturbed_night"),
        ("slept_in_bed", "slept_in_chair"), ("daytime_awake", "awake_most_of_night"),
    ),
    "pain_report": (
        ("pain_reported", "pain_denied"), ("mild_pain", "severe_pain"),
        ("pain_improved", "pain_worsened"), ("no_pain_at_rest", "pain_at_rest"),
        ("pain_on_movement", "no_pain_on_movement"), ("headache_reported", "headache_denied"),
        ("joint_pain_reported", "joint_pain_denied"), ("pain_scale_four", "pain_scale_zero"),
        ("requested_comfort", "declined_comfort"), ("pain_relief_reported", "no_relief_reported"),
    ),
    "appointment_attendance": (
        ("attended", "not_attended"), ("arrived_on_time", "did_not_arrive"),
        ("seen_by_specialist", "not_seen_by_specialist"), ("appointment_completed", "appointment_cancelled"),
        ("transport_arranged", "transport_not_arranged"), ("family_accompanied", "attended_alone"),
        ("review_occurred", "review_not_confirmed"), ("discharge_given", "no_discharge_record"),
        ("follow_up_booked", "no_follow_up_booked"), ("clinic_called_back", "no_callback_received"),
    ),
    "fluid_intake": (
        ("adequate_fluids", "low_fluids"), ("drank_two_cups", "drank_half_cup"),
        ("finished_water", "water_untouched"), ("accepted_drink", "refused_drink"),
        ("urine_output_reported_normal", "reduced_output_reported"), ("drank_with_meals", "no_drinks_with_meals"),
        ("needed_prompting_to_drink", "drank_without_prompting"), ("oral_fluids_taken", "oral_fluids_not_taken"),
        ("fluid_chart_complete", "fluid_chart_incomplete"), ("usual_intake", "below_usual_intake"),
    ),
    "location": (
        ("at_home", "at_hospital"), ("in_bedroom", "in_living_room"),
        ("in_day_centre", "not_at_day_centre"), ("with_daughter", "with_son"),
        ("at_clinic", "at_home"), ("in_shared_room", "in_private_room"),
        ("at_residence", "at_temporary_address"), ("indoors", "outdoors"),
        ("at_known_location", "location_not_confirmed"), ("returned_home", "return_home_not_confirmed"),
    ),
    "personal_care": (
        ("completed_wash", "wash_not_completed"), ("changed_clothes", "clothes_not_changed"),
        ("brushed_teeth", "teeth_not_brushed"), ("accepted_bath", "declined_bath"),
        ("used_toilet", "toileting_assistance_needed"), ("dressed_independently", "dressing_assisted"),
        ("skin_checked", "skin_check_not_done"), ("hair_washed", "hair_not_washed"),
        ("personal_care_with_one_prompt", "personal_care_refused"), ("usual_routine_completed", "routine_incomplete"),
    ),
    "mood_observation": (
        ("calm", "agitated"), ("engaged", "withdrawn"),
        ("appeared_content", "appeared_distressed"), ("recognised_family", "did_not_recognise_family"),
        ("conversation_coherent", "conversation_disjointed"), ("no_distress_reported", "distress_reported"),
        ("accepted_reassurance", "reassurance_not_effective"), ("usual_mood", "mood_changed"),
        ("participated_in_activity", "declined_activity"), ("settled", "restless"),
    ),
}
SCENARIOS = [
    pytest.param(field, first, second, index, id=f"MC-{index:03d}-{field}")
    for index, (field, (first, second)) in enumerate(
        ((field, PAIRS_BY_FIELD[field][offset])
         for field in FIELDS
         for offset in range(10)),
        start=1,
    )
]


@pytest.fixture(scope="session", autouse=True)
def schema_ready():
    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(bind=engine)
    yield
    if engine.dialect.name == "sqlite":
        Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.mark.parametrize("field,first_value,second_value,case_number", SCENARIOS)
def test_100_conflicting_reports_survive_commit_and_fresh_session(
    field, first_value, second_value, case_number
):
    """Two reporters remain distinct; neither conflicting value is silently selected."""
    suffix = hashlib.sha256(f"messy-care-{case_number}".encode()).hexdigest()[:16]
    person_id = f"mc-p-{suffix}"
    caregiver_a_id = f"mc-a-{suffix}"
    caregiver_b_id = f"mc-b-{suffix}"
    observed_at = datetime(2026, 10, 20, 8, tzinfo=timezone.utc) + timedelta(minutes=case_number)

    with SessionLocal() as writer:
        person = Person(id=person_id, name=f"Synthetic recipient {case_number}")
        writer.add(person)
        writer.flush()
        writer.add_all([
            Caregiver(id=caregiver_a_id, name="Reporter A", relationship="Family",
                      person_id=person_id, email=f"a-{suffix}@benchmark.invalid"),
            Caregiver(id=caregiver_b_id, name="Reporter B", relationship="Family",
                      person_id=person_id, email=f"b-{suffix}@benchmark.invalid"),
        ])
        writer.flush()
        evidence_a_id = f"mc-ea-{suffix}"
        evidence_b_id = f"mc-eb-{suffix}"
        writer.add_all([
            Evidence(
                id=evidence_a_id, person_id=person_id, type="note",
                source_text=f"Reporter A source: {first_value}",
                uploaded_by_caregiver_id=caregiver_a_id,
                original_author="Reporter A", reported_by_label="Reporter A",
                reported_by_caregiver_id=caregiver_a_id,
                collected_by_caregiver_id=caregiver_a_id,
                occurred_at=observed_at, reported_at=observed_at + timedelta(minutes=2),
                original_source_reference=f"benchmark://{suffix}/report-a",
            ),
            Evidence(
                id=evidence_b_id, person_id=person_id, type="note",
                source_text=f"Reporter B source: {second_value}",
                uploaded_by_caregiver_id=caregiver_b_id,
                original_author="Reporter B", reported_by_label="Reporter B",
                reported_by_caregiver_id=caregiver_b_id,
                collected_by_caregiver_id=caregiver_b_id,
                occurred_at=observed_at + timedelta(minutes=1),
                reported_at=observed_at + timedelta(minutes=3),
                original_source_reference=f"benchmark://{suffix}/report-b",
            ),
        ])
        writer.flush()

        first = create_assertion(writer, CareAssertionCreate(
            person_id=person_id, subject_type="care_recipient", subject_id=person_id,
            field=field, asserted_value=first_value,
            source_label=f"Reporter A / case {case_number}",
            source_text=f"Reporter A states: {first_value}",
            evidence_ids=[evidence_a_id],
            observed_at=observed_at, created_by_caregiver_id=caregiver_a_id,
        ), commit=False)
        second = create_assertion(writer, CareAssertionCreate(
            person_id=person_id, subject_type="care_recipient", subject_id=person_id,
            field=field, asserted_value=second_value,
            source_label=f"Reporter B / case {case_number}",
            source_text=f"Reporter B states: {second_value}",
            evidence_ids=[evidence_b_id],
            observed_at=observed_at + timedelta(minutes=1),
            created_by_caregiver_id=caregiver_b_id,
        ), commit=False)
        writer.commit()
        first_id, second_id = first.id, second.id

    # A genuinely new ORM session proves the result is database-backed, not identity-map state.
    with SessionLocal() as reader:
        reports = reader.query(CareAssertion).filter(
            CareAssertion.person_id == person_id,
            CareAssertion.field == field,
        ).order_by(CareAssertion.source_label.asc()).all()
        assert {row.id for row in reports} == {first_id, second_id}
        by_value = {row.asserted_value: row for row in reports}
        assert set(by_value) == {first_value, second_value}
        assert by_value[first_value].created_by_caregiver_id == caregiver_a_id
        assert by_value[second_value].created_by_caregiver_id == caregiver_b_id
        assert by_value[first_value].source_label.startswith("Reporter A")
        assert by_value[second_value].source_label.startswith("Reporter B")
        assert by_value[first_value].evidence_ids == [evidence_a_id]
        assert by_value[second_value].evidence_ids == [evidence_b_id]
        assert by_value[first_value].observed_at == observed_at
        assert by_value[second_value].observed_at == observed_at + timedelta(minutes=1)
        evidence_rows = reader.query(Evidence).filter(
            Evidence.person_id == person_id,
            Evidence.id.in_([evidence_a_id, evidence_b_id]),
        ).all()
        assert {row.id for row in evidence_rows} == {evidence_a_id, evidence_b_id}
        evidence_by_id = {row.id: row for row in evidence_rows}
        assert evidence_by_id[evidence_a_id].reported_by_caregiver_id == caregiver_a_id
        assert evidence_by_id[evidence_b_id].reported_by_caregiver_id == caregiver_b_id
        assert evidence_by_id[evidence_a_id].original_author == "Reporter A"
        assert evidence_by_id[evidence_b_id].original_author == "Reporter B"
        for row in evidence_rows:
            recorded = row.ingested_at
            if recorded.tzinfo is None:
                recorded = recorded.replace(tzinfo=timezone.utc)
            occurred = row.occurred_at
            if occurred.tzinfo is None:
                occurred = occurred.replace(tzinfo=timezone.utc)
            assert recorded > occurred, "ingestion time must remain distinct from event time"
        assert by_value[first_value].assertion_status == "reported"
        assert by_value[second_value].assertion_status == "reported"

        contradiction = reader.query(Contradiction).filter(
            Contradiction.person_id == person_id,
            Contradiction.subject_id == person_id,
            Contradiction.field == field,
            Contradiction.resolution_status == "unresolved",
        ).one()
        assert set(contradiction.source_assertion_ids or []) == {first_id, second_id}
        assert first_value in contradiction.description
        assert second_value in contradiction.description

        revisions = reader.query(CareStateRevision).filter(
            CareStateRevision.person_id == person_id,
            CareStateRevision.target_type == "care_assertions",
            CareStateRevision.target_id.in_([first_id, second_id]),
        ).all()
        assert {row.target_id for row in revisions} == {first_id, second_id}
        assert all(row.revision_number == 1 for row in revisions)
