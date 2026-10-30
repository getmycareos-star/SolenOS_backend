"""100 independently identified, PostgreSQL-backed messy-care conflict journeys.

Each case writes two conflicting, separately attributed reports through the production
care-assertion service, commits them, closes the writer session, and reconstructs the
record from a new SQLAlchemy session. These are synthetic software qualification cases,
not clinical validation or 100 different clinical phenotypes.
"""
from __future__ import annotations

import hashlib
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.main import app  # noqa: F401 - register all ORM models
from app.models.care import Caregiver, Person
from app.models.care_continuity import CareAssertion
from app.models.uncertainty import Contradiction
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion

DATABASE_URL = os.getenv("SOLENOS_BENCHMARK_DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "The 100-journey benchmark requires SOLENOS_BENCHMARK_DATABASE_URL; "
        "SQLite fallback is intentionally prohibited."
    )

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

FIELDS = (
    "current_location", "medication_status", "appointment_attendance",
    "meal_intake", "sleep_quality", "mobility_support", "pain_report",
    "mood_report", "fall_occurrence", "care_plan_status",
)
REPORT_PAIRS = (
    ("Caregiver A", "Caregiver B"),
    ("Daughter", "Son"),
    ("Morning carer", "Evening carer"),
    ("Family member 1", "Family member 2"),
    ("Caregiver report", "Professional note"),
    ("Phone report", "In-person report"),
    ("Handwritten diary", "Message record"),
    ("First observer", "Second observer"),
    ("Earlier handoff", "Later handoff"),
    ("Primary caregiver", "Relief caregiver"),
)
SCENARIOS = [
    pytest.param(index, FIELDS[index // 10], REPORT_PAIRS[index % 10], id=f"MC-{index + 1:03d}")
    for index in range(100)
]


@pytest.fixture(scope="session", autouse=True)
def benchmark_schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.mark.parametrize("case_number,field,reporters", SCENARIOS)
def test_100_conflicting_reports_survive_commit_and_fresh_session(
    case_number, field, reporters
):
    """MC-NNN: preserve both reports, attribution, unresolved conflict and record scope."""
    suffix = hashlib.sha256(f"messy-care-{case_number}".encode()).hexdigest()[:12]
    person_id = f"p-{suffix}"
    caregiver_id = f"c-{suffix}"
    first_value = f"case-{case_number}-reported-state-A"
    second_value = f"case-{case_number}-reported-state-B"

    writer = SessionLocal()
    try:
        person = Person(id=person_id, name=f"Synthetic recipient {case_number}")
        caregiver = Caregiver(
            id=caregiver_id,
            name=f"Synthetic caregiver {case_number}",
            relationship="Family",
            person_id=person_id,
        )
        writer.add_all([person, caregiver])
        writer.commit()

        first = create_assertion(
            writer,
            CareAssertionCreate(
                person_id=person_id,
                subject_type="care_recipient",
                subject_id=person_id,
                field=field,
                asserted_value=first_value,
                source_label=reporters[0],
                source_text=f"{reporters[0]} reports {first_value}.",
                created_by_caregiver_id=caregiver_id,
            ),
            commit=False,
        )
        second = create_assertion(
            writer,
            CareAssertionCreate(
                person_id=person_id,
                subject_type="care_recipient",
                subject_id=person_id,
                field=field,
                asserted_value=second_value,
                source_label=reporters[1],
                source_text=f"{reporters[1]} reports {second_value}.",
                created_by_caregiver_id=caregiver_id,
            ),
            commit=False,
        )
        first_id, second_id = first.id, second.id
        writer.commit()
    finally:
        writer.close()

    # Deliberately reconstruct after the writer session is gone.
    reader = SessionLocal()
    try:
        reports = (
            reader.query(CareAssertion)
            .filter(CareAssertion.person_id == person_id, CareAssertion.field == field)
            .order_by(CareAssertion.recorded_at, CareAssertion.id)
            .all()
        )
        assert {row.id for row in reports} == {first_id, second_id}
        assert {row.asserted_value for row in reports} == {first_value, second_value}
        assert {row.source_label for row in reports} == set(reporters)
        assert all(row.assertion_status == "reported" for row in reports)

        conflicts = (
            reader.query(Contradiction)
            .filter(
                Contradiction.person_id == person_id,
                Contradiction.subject_id == person_id,
                Contradiction.field == field,
                Contradiction.resolution_status == "unresolved",
            )
            .all()
        )
        assert len(conflicts) == 1
        assert set(conflicts[0].source_assertion_ids or []) == {first_id, second_id}
        assert first_value in conflicts[0].description
        assert second_value in conflicts[0].description
    finally:
        reader.close()
