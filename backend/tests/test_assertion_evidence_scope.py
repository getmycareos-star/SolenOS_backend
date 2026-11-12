from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.care import Caregiver, Evidence, Person
from app.schemas.care_continuity import CareAssertionCreate
from app.services.care_continuity import create_assertion


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def setup_function():
    Base.metadata.create_all(bind=engine)


def teardown_function():
    Base.metadata.drop_all(bind=engine)


def test_assertion_evidence_links_must_belong_to_active_care_record():
    db = TestingSessionLocal()
    try:
        for person_id, caregiver_id in (
            ("assertion-person-a", "assertion-caregiver-a"),
            ("assertion-person-b", "assertion-caregiver-b"),
        ):
            db.add(Person(id=person_id, name=person_id))
            db.add(Caregiver(
                id=caregiver_id,
                name=caregiver_id,
                email=f"{caregiver_id}@example.invalid",
                relationship="Family",
                person_id=person_id,
            ))
        db.flush()
        db.add_all([
            Evidence(
                id="assertion-evidence-a",
                person_id="assertion-person-a",
                type="note",
                evidence_kind="caregiver_report",
                epistemic_status="caregiver_reported",
                source_text="Caregiver observed reduced meal intake.",
                uploaded_by_caregiver_id="assertion-caregiver-a",
                reported_by_caregiver_id="assertion-caregiver-a",
                reported_by_label="Caregiver",
                source_type="caregiver_report",
            ),
            Evidence(
                id="assertion-evidence-b",
                person_id="assertion-person-b",
                type="note",
                evidence_kind="caregiver_report",
                epistemic_status="caregiver_reported",
                source_text="Private note from another care record.",
                uploaded_by_caregiver_id="assertion-caregiver-b",
                reported_by_caregiver_id="assertion-caregiver-b",
                reported_by_label="Caregiver",
                source_type="caregiver_report",
            ),
        ])
        db.commit()

        payload = {
            "person_id": "assertion-person-a",
            "subject_type": "care_recipient",
            "subject_id": "assertion-person-a",
            "field": "meal_intake",
            "assertion_kind": "time_bound_observation",
            "epistemic_state": "observed",
            "asserted_value": "Left half the meal",
            "source_label": "Caregiver observation",
            "source_text": "Half the meal remained after eating.",
            "observed_at": datetime.now(timezone.utc),
        }
        foreign = CareAssertionCreate(**{**payload, "evidence_ids": ["assertion-evidence-b"]})
        with pytest.raises(HTTPException) as error:
            create_assertion(db, foreign)
        assert error.value.status_code == 422
        assert "belong to the same care record" in error.value.detail

        accepted = create_assertion(
            db,
            CareAssertionCreate(**{**payload, "evidence_ids": ["assertion-evidence-a"]}),
        )
        assert accepted.epistemic_state == "observed"
        assert accepted.assertion_status == "reported"
        assert accepted.evidence_ids == ["assertion-evidence-a"]
    finally:
        db.close()
