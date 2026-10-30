"""PostgreSQL regression for forward repair of partially-created evidence tables.

This fixture creates real legacy rows, removes only the three columns that were
missing in production, applies the forward migration, and proves the original
rows and values survive unchanged.
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
    raise SystemExit("Refusing evidence-column migration regression outside PostgreSQL.")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
PERSON = "evidence-repair-person"
CAREGIVER = "evidence-repair-caregiver"
EVIDENCE = "evidence-repair-source"
CLAIM = "evidence-repair-claim"
ASSESSMENT = "evidence-repair-assessment"
OBSERVATION = "evidence-repair-observation"


def seed() -> None:
    from app.models.care import Caregiver, Evidence, Person
    from app.models.care_continuity import CareAssertion, EvidenceClaimAssessment, EvidenceObservation

    with Session(engine) as db:
        person = Person(id=PERSON, name="Synthetic evidence migration fixture")
        db.add(person)
        db.flush()
        caregiver = Caregiver(
            id=CAREGIVER, name="Synthetic fixture caregiver",
            relationship="family", person_id=PERSON,
        )
        db.add(caregiver)
        db.flush()
        evidence = Evidence(
            id=EVIDENCE, person_id=PERSON, type="document",
            source_text="Synthetic source text; not a real care record.",
            uploaded_by_caregiver_id=CAREGIVER,
        )
        claim = CareAssertion(
            id=CLAIM, person_id=PERSON, subject_type="person", subject_id=PERSON,
            field="fixture_status", asserted_value="reported",
            source_label="Synthetic migration fixture",
            source_text="Synthetic source text; not a real care record.",
            evidence_ids=[], assertion_status="reported",
            created_by_caregiver_id=CAREGIVER,
        )
        db.add_all([evidence, claim])
        db.commit()

        db.add(EvidenceClaimAssessment(
            id=ASSESSMENT, person_id=PERSON, claim_id=CLAIM, claim_version=1,
            status="unverified", rationale="Preserve this legacy assessment.",
            policy_version="fixture-v1", evidence_links=[],
            reviewed_by_caregiver_id=CAREGIVER,
        ))
        db.add(EvidenceObservation(
            id=OBSERVATION, person_id=PERSON, evidence_id=EVIDENCE,
            observation_kind="reported", epistemic_status="reported",
            subject_label="fixture subject", attribute="fixture attribute",
            value_text="original value", source_passage="original value",
            source_span_start=0, source_span_end=14,
            extraction_method="fixture", extraction_version="fixture-v1",
            created_by_caregiver_id=CAREGIVER,
        ))
        db.commit()

    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE evidence_claim_assessments DROP COLUMN IF EXISTS superseded_by_claim_id"
        ))
        connection.execute(text(
            "ALTER TABLE evidence_observations DROP COLUMN IF EXISTS supersedes_observation_id"
        ))
        connection.execute(text(
            "ALTER TABLE evidence_observations DROP COLUMN IF EXISTS correction_rationale"
        ))

    inspector = inspect(engine)
    assert "superseded_by_claim_id" not in {
        item["name"] for item in inspector.get_columns("evidence_claim_assessments")
    }
    observation_columns = {item["name"] for item in inspector.get_columns("evidence_observations")}
    assert "supersedes_observation_id" not in observation_columns
    assert "correction_rationale" not in observation_columns
    print("PASS: seeded existing assessment and observation rows, then removed only legacy-missing columns.")


def verify() -> None:
    with engine.connect() as connection:
        assessment = connection.execute(text("""
            SELECT person_id, claim_id, claim_version, status, rationale, policy_version
            FROM evidence_claim_assessments WHERE id = :id
        """), {"id": ASSESSMENT}).one()
        assert tuple(assessment) == (
            PERSON, CLAIM, 1, "unverified",
            "Preserve this legacy assessment.", "fixture-v1",
        ), f"Legacy claim assessment changed: {assessment}"

        observation = connection.execute(text("""
            SELECT person_id, evidence_id, observation_kind, epistemic_status,
                   subject_label, attribute, value_text, source_passage,
                   source_span_start, source_span_end
            FROM evidence_observations WHERE id = :id
        """), {"id": OBSERVATION}).one()
        assert tuple(observation) == (
            PERSON, EVIDENCE, "reported", "reported", "fixture subject",
            "fixture attribute", "original value", "original value", 0, 14,
        ), f"Legacy observation changed: {observation}"

    inspector = inspect(engine)
    claim_columns = {item["name"] for item in inspector.get_columns("evidence_claim_assessments")}
    observation_columns = {item["name"] for item in inspector.get_columns("evidence_observations")}
    assert "superseded_by_claim_id" in claim_columns
    assert {"supersedes_observation_id", "correction_rationale"} <= observation_columns

    claim_fk = inspector.get_foreign_keys("evidence_claim_assessments")
    assert any(
        "superseded_by_claim_id" in (item.get("constrained_columns") or [])
        and item.get("referred_table") == "care_assertions"
        and (item.get("referred_columns") or []) == ["id"]
        for item in claim_fk
    ), "claim supersession foreign key missing"

    observation_fk = inspector.get_foreign_keys("evidence_observations")
    assert any(
        "supersedes_observation_id" in (item.get("constrained_columns") or [])
        and item.get("referred_table") == "evidence_observations"
        and (item.get("referred_columns") or []) == ["id"]
        for item in observation_fk
    ), "observation correction foreign key missing"

    for table, names in (
        ("evidence_claim_assessments", ("superseded_by_claim_id",)),
        ("evidence_observations", ("supersedes_observation_id", "correction_rationale")),
    ):
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        for name in names:
            assert columns[name]["nullable"] is True, f"{table}.{name} must remain nullable"
    print("PASS: legacy assessment/observation rows preserved; missing nullable columns and foreign keys repaired.")


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in {"seed", "verify"}:
            raise SystemExit("Usage: postgres_evidence_column_reconciliation.py seed|verify")
        {"seed": seed, "verify": verify}[sys.argv[1]]()
    finally:
        engine.dispose()
