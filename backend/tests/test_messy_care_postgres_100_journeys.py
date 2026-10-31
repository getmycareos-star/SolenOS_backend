"""One hundred independently identified, PostgreSQL-backed contradiction journeys.

Each case creates a care recipient, records two distinct source reports that disagree
about one current-state field, commits both reports, closes the writer session, and
reconstructs the contradiction from a new database session. No inference model or
mock repository is used. These are synthetic engineering cases, not clinical validation.
"""
from datetime import datetime, timedelta, timezone
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.main import app  # noqa: F401 - register all ORM models
from app.models.care import Person, Caregiver
from app.models.care_continuity import CareAssertion
from app.models.uncertainty import Contradiction
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion


DATABASE_URL = os.getenv("SOLENOS_BENCHMARK_DATABASE_URL")
if not DATABASE_URL or not DATABASE_URL.startswith("postgresql"):
    raise RuntimeError(
        "This qualification suite requires SOLENOS_BENCHMARK_DATABASE_URL "
        "pointing to PostgreSQL; SQLite fallback is deliberately forbidden."
    )

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

FIELDS = (
    "current_location", "medication_taken", "sleep_quality", "meal_intake",
    "mobility_status", "pain_report", "appointment_attendance", "fluid_intake",
    "mood_report", "night_waking",
)
VALUES = (
    ("At home", "At hospital"), ("Taken", "Not taken"),
    ("Slept well", "Woke repeatedly"), ("Ate full meal", "Ate very little"),
    ("Walked independently", "Needed assistance"), ("No pain reported", "Pain reported"),
    ("Attendance confirmed", "Attendance not confirmed"),
    ("Adequate fluids reported", "Low fluid intake reported"),
    ("Calm", "Distressed"), ("No waking reported", "Waking reported"),
)
SCENARIOS = tuple(
    {
        "scenario_id": f"MC-PG-{i:03d}",
        "field": FIELDS[(i - 1) % len(FIELDS)],
        "left": VALUES[(i - 1) % len(VALUES)][0],
        "right": VALUES[(i - 1) % len(VALUES)][1],
        "day_offset": (i % 27) + 1,
        "reporter_a": f"Reporter A case {i:03d}",
        "reporter_b": f"Reporter B case {i:03d}",
    }
    for i in range(1, 101)
)
assert len(SCENARIOS) == 100
assert len({item["scenario_id"] for item in SCENARIOS}) == 100


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.mark.parametrize("case", SCENARIOS, ids=lambda item: item["scenario_id"])
def test_postgres_contradiction_journey_survives_fresh_session(case, db):
    """Conflicting reports remain separately attributable and unresolved after reconnect."""
    suffix = case["scenario_id"].lower().replace("-", "")
    person_id = f"p-{suffix}"
    caregiver_id = f"c-{suffix}"
    person = Person(id=person_id, name=f"Synthetic recipient {case['scenario_id']}")
    db.add(person)
    db.flush()
    caregiver = Caregiver(
        id=caregiver_id,
        name=f"Synthetic caregiver {case['scenario_id']}",
        email=f"{suffix}@benchmark.invalid",
        relationship="Family",
        person_id=person_id,
    )
    db.add(caregiver)
    db.commit()

    observed_at = datetime.now(timezone.utc) - timedelta(days=case["day_offset"])
    first = create_assertion(
        db,
        CareAssertionCreate(
            person_id=person_id, subject_type="care_recipient", subject_id=person_id,
            field=case["field"], asserted_value=case["left"],
            source_label=case["reporter_a"],
            source_text=f"{case['reporter_a']} reported: {case['left']}.",
            observed_at=observed_at, created_by_caregiver_id=caregiver_id,
        ),
    )
    second = create_assertion(
        db,
        CareAssertionCreate(
            person_id=person_id, subject_type="care_recipient", subject_id=person_id,
            field=case["field"], asserted_value=case["right"],
            source_label=case["reporter_b"],
            source_text=f"{case['reporter_b']} reported: {case['right']}.",
            observed_at=observed_at + timedelta(minutes=7),
            created_by_caregiver_id=caregiver_id,
        ),
    )
    first_id, second_id = first.id, second.id
    db.commit()
    db.close()

    # Reconnect with a genuinely new SQLAlchemy Session and re-read persisted rows.
    with SessionFactory() as reader:
        reports = (
            reader.query(CareAssertion)
            .filter(CareAssertion.person_id == person_id)
            .order_by(CareAssertion.recorded_at.asc(), CareAssertion.id.asc())
            .all()
        )
        assert len(reports) == 2, case["scenario_id"]
        by_id = {item.id: item for item in reports}
        assert set(by_id) == {first_id, second_id}, case["scenario_id"]
        assert by_id[first_id].source_label == case["reporter_a"]
        assert by_id[first_id].asserted_value == case["left"]
        assert by_id[second_id].source_label == case["reporter_b"]
        assert by_id[second_id].asserted_value == case["right"]
        assert by_id[first_id].observed_at != by_id[second_id].observed_at
        assert all(item.assertion_status == "reported" for item in reports)

        conflicts = (
            reader.query(Contradiction)
            .filter(
                Contradiction.person_id == person_id,
                Contradiction.subject_id == person_id,
                Contradiction.field == case["field"],
                Contradiction.resolution_status == "unresolved",
            )
            .all()
        )
        assert len(conflicts) == 1, case["scenario_id"]
        conflict = conflicts[0]
        assert set(conflict.source_assertion_ids or []) == {first_id, second_id}
        assert conflict.resolution_status == "unresolved"
        assert case["left"] in conflict.description
        assert case["right"] in conflict.description
        assert "no assertion was selected as true" in conflict.description.lower()
