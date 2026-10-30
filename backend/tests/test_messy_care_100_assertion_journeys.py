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
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app  # noqa: F401 - registers all ORM models and revision listeners
from app.models.care import Caregiver, Person
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
PAIRS = (
    ("given", "not_given"), ("independent", "needs_assistance"),
    ("ate_full_meal", "ate_few_bites"), ("slept_through", "woke_repeatedly"),
    ("pain_reported", "pain_denied"), ("attended", "not_attended"),
    ("adequate_fluids", "low_fluids"), ("at_home", "at_hospital"),
    ("completed", "not_completed"), ("calm", "agitated"),
)
SCENARIOS = [
    pytest.param(field, first, second, index, id=f"MC-{index:03d}-{field}")
    for index, (field, (first, second)) in enumerate(
        ((field, PAIRS[(field_index + offset) % len(PAIRS)])
         for field_index, field in enumerate(FIELDS)
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

        first = create_assertion(writer, CareAssertionCreate(
            person_id=person_id, subject_type="care_recipient", subject_id=person_id,
            field=field, asserted_value=first_value,
            source_label=f"Reporter A / case {case_number}",
            source_text=f"Reporter A states: {first_value}",
            observed_at=observed_at, created_by_caregiver_id=caregiver_a_id,
        ), commit=False)
        second = create_assertion(writer, CareAssertionCreate(
            person_id=person_id, subject_type="care_recipient", subject_id=person_id,
            field=field, asserted_value=second_value,
            source_label=f"Reporter B / case {case_number}",
            source_text=f"Reporter B states: {second_value}",
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
