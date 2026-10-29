"""Prevent concurrent creation of duplicate active longitudinal patterns.

Revision ID: 20261029_01_pattern_unique
Revises: 20261028_06_review_immutable

The migration refuses to guess which active duplicate is authoritative. If existing
rows violate the invariant, deployment stops with an explicit error and the records
must be reconciled with provenance preserved before retrying.
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261029_01_pattern_unique"
down_revision = "20261028_06_review_immutable"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_patterns_one_active_per_subject"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("patterns"):
        raise RuntimeError("The patterns table must exist before active-pattern uniqueness is enforced")

    duplicate = bind.execute(text("""
        SELECT person_id, subject_type, subject_id, pattern_type, COUNT(*) AS duplicate_count
        FROM patterns
        WHERE is_active IS TRUE
        GROUP BY person_id, subject_type, subject_id, pattern_type
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).mappings().first()
    if duplicate:
        raise RuntimeError(
            "Cannot enforce one active pattern per subject: existing duplicate active "
            f"patterns found for person_id={duplicate['person_id']}, "
            f"subject_type={duplicate['subject_type']}, subject_id={duplicate['subject_id']}, "
            f"pattern_type={duplicate['pattern_type']}, count={duplicate['duplicate_count']}. "
            "Reconcile the records while preserving history, then rerun the migration."
        )

    existing_indexes = {item["name"] for item in inspector.get_indexes("patterns")}
    if INDEX_NAME not in existing_indexes:
        op.create_index(
            INDEX_NAME,
            "patterns",
            ["person_id", "subject_type", "subject_id", "pattern_type"],
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
            sqlite_where=text("is_active IS TRUE"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("patterns"):
        existing_indexes = {item["name"] for item in inspect(bind).get_indexes("patterns")}
        if INDEX_NAME in existing_indexes:
            op.drop_index(INDEX_NAME, table_name="patterns")
