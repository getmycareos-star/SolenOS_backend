"""Regression fixture for a partially pre-created PostgreSQL freshness schema.

The seed mode prepares revision 24, inserts existing care and freshness rows, and
leaves source_time_kind absent from the assessment table. Verify mode runs after
Alembic upgrade head and proves the rows survived and the missing schema objects
were added without replacing the tables.
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
    raise SystemExit("Refusing freshness migration regression outside PostgreSQL.")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
PERSON = "freshness-existing-person"
CAREGIVER = "freshness-existing-caregiver"
EVIDENCE = "freshness-existing-evidence"
POLICY = "freshness-existing-policy"
ASSESSMENT = "freshness-existing-assessment"


def seed() -> None:
    from app.models.care import Caregiver, Evidence, Person

    with Session(engine) as db:
        db.add(Person(id=PERSON, name="Freshness migration preservation fixture"))
        db.flush()
        db.add(Caregiver(
            id=CAREGIVER,
            name="Freshness migration fixture caregiver",
            relationship="family",
            person_id=PERSON,
        ))
        db.flush()
        db.add(Evidence(
            id=EVIDENCE,
            person_id=PERSON,
            type="document",
            source_text="Synthetic migration regression evidence; not real care data.",
            uploaded_by_caregiver_id=CAREGIVER,
        ))
        db.commit()

    # The 20261022_01 baseline uses Base.metadata.create_all(), which
    # pre-creates all currently registered ORM tables on a fresh database.
    # Reconcile those existing tables rather than creating them a second time.
    inspector = inspect(engine)
    for table in ("evidence_freshness_policies", "evidence_freshness_assessments"):
        assert inspector.has_table(table), f"baseline did not create expected ORM table {table}"

    with engine.begin() as connection:
        # Simulate an older partial assessment schema while preserving the
        # original table and its existing data.
        connection.execute(text("""
            ALTER TABLE evidence_freshness_assessments
            DROP COLUMN IF EXISTS source_time_kind
        """))
        for index in (
            "ix_evidence_freshness_policies_person_id",
            "ix_evidence_freshness_policies_person_domain",
            "ix_evidence_freshness_assessments_person_id",
            "ix_evidence_freshness_assessments_person_evidence",
            "ix_evidence_freshness_assessments_policy",
        ):
            connection.execute(text(f'DROP INDEX IF EXISTS "{index}"'))
        connection.execute(text("""
            INSERT INTO evidence_freshness_policies
                (id, person_id, domain_key, policy_version, max_age_days,
                 historical_after_days, rationale, created_by_caregiver_id)
            VALUES
                (:id, :person, 'document_history', 'legacy-v1', 30, 180,
                 'Pre-existing policy row must remain unchanged.', :caregiver)
        """), {"id": POLICY, "person": PERSON, "caregiver": CAREGIVER})
        connection.execute(text("""
            INSERT INTO evidence_freshness_assessments
                (id, person_id, evidence_id, policy_id, policy_version,
                 freshness_status, source_time, assessed_at, age_days,
                 rationale, assessed_by_caregiver_id)
            VALUES
                (:id, :person, :evidence, :policy, 'legacy-v1',
                 'unknown_source_time', NULL, now(), NULL,
                 'Pre-existing assessment row must remain unchanged.', :caregiver)
        """), {
            "id": ASSESSMENT, "person": PERSON, "evidence": EVIDENCE,
            "policy": POLICY, "caregiver": CAREGIVER,
        })


def verify() -> None:
    with engine.connect() as connection:
        policy = connection.execute(text("""
            SELECT person_id, domain_key, policy_version, rationale
            FROM evidence_freshness_policies WHERE id = :id
        """), {"id": POLICY}).one()
        assert tuple(policy) == (
            PERSON, "document_history", "legacy-v1",
            "Pre-existing policy row must remain unchanged.",
        ), f"Existing freshness policy data changed: {policy}"

        assessment = connection.execute(text("""
            SELECT person_id, evidence_id, policy_id, policy_version,
                   freshness_status, rationale
            FROM evidence_freshness_assessments WHERE id = :id
        """), {"id": ASSESSMENT}).one()
        assert tuple(assessment) == (
            PERSON, EVIDENCE, POLICY, "legacy-v1", "unknown_source_time",
            "Pre-existing assessment row must remain unchanged.",
        ), f"Existing freshness assessment data changed: {assessment}"

    inspector = inspect(engine)
    assessment_columns = {
        column["name"] for column in inspector.get_columns("evidence_freshness_assessments")
    }
    assert "source_time_kind" in assessment_columns, "missing assessment column was not added"
    for name, table in (
        ("ix_evidence_freshness_policies_person_id", "evidence_freshness_policies"),
        ("ix_evidence_freshness_policies_person_domain", "evidence_freshness_policies"),
        ("ix_evidence_freshness_assessments_person_id", "evidence_freshness_assessments"),
        ("ix_evidence_freshness_assessments_person_evidence", "evidence_freshness_assessments"),
        ("ix_evidence_freshness_assessments_policy", "evidence_freshness_assessments"),
    ):
        assert name in {item["name"] for item in inspector.get_indexes(table)}, (
            f"missing expected index {name}"
        )
    with engine.connect() as connection:
        for table in ("evidence_freshness_policies", "evidence_freshness_assessments"):
            trigger = connection.execute(text("""
                SELECT 1 FROM pg_trigger
                WHERE tgname = :name AND NOT tgisinternal
            """), {"name": f"trg_{table}_immutable"}).first()
            assert trigger is not None, f"missing append-only trigger for {table}"
    print("PASS: existing freshness rows preserved; missing column, indexes, and immutable triggers reconciled.")


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in {"seed", "verify"}:
            raise SystemExit("Usage: postgres_freshness_existing_schema.py seed|verify")
        {"seed": seed, "verify": verify}[sys.argv[1]]()
    finally:
        engine.dispose()
