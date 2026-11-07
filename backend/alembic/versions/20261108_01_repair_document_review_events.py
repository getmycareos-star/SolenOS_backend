"""Repair document review schema drift before readiness can pass.

Revision ID: 20261108_01_doc_review_repair
Revises: 20261107_02_handoff_drafts

The production readiness probe has repeatedly found document_review_events
missing even though the prior migration revision is recorded as current.
This forward-only reconciliation creates the table when absent, verifies the
full contract, and restores the append-only database trigger. It never reads,
rewrites, or deletes care records.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261108_01_doc_review_repair"
down_revision = "20261107_02_handoff_drafts"
branch_labels = None
depends_on = None


_REQUIRED_COLUMNS = {
    "id", "analysis_id", "person_id", "caregiver_id", "source_evidence_id",
    "candidate_index", "decision_version", "decision", "rationale",
    "before_state", "after_state", "resulting_assertion_id", "created_at",
}
_REQUIRED_FOREIGN_KEYS = {
    ("analysis_id", "capture_analyses"),
    ("person_id", "persons"),
    ("caregiver_id", "caregivers"),
    ("source_evidence_id", "evidence"),
    ("resulting_assertion_id", "care_assertions"),
}


def _create_table(bind) -> None:
    inspector = inspect(bind)
    if inspector.has_table("document_review_events"):
        return

    required_parents = {
        "capture_analyses", "persons", "caregivers", "evidence", "care_assertions",
    }
    missing_parents = sorted(name for name in required_parents if not inspector.has_table(name))
    if missing_parents:
        raise RuntimeError(
            "Cannot repair document_review_events; required parent tables are missing: "
            + ", ".join(missing_parents)
        )

    op.create_table(
        "document_review_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("analysis_id", sa.String(), sa.ForeignKey("capture_analyses.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("source_evidence_id", sa.String(), sa.ForeignKey("evidence.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("candidate_index", sa.Integer(), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("before_state", sa.JSON(), nullable=False),
        sa.Column("after_state", sa.JSON(), nullable=False),
        sa.Column("resulting_assertion_id", sa.String(), sa.ForeignKey("care_assertions.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "analysis_id", "candidate_index", "decision_version",
            name="uq_document_review_candidate_version",
        ),
    )


def _verify_contract(bind) -> None:
    inspector = inspect(bind)
    if not inspector.has_table("document_review_events"):
        raise RuntimeError("document_review_events was not created")

    columns = {item["name"] for item in inspector.get_columns("document_review_events")}
    missing_columns = _REQUIRED_COLUMNS - columns
    if missing_columns:
        raise RuntimeError(
            "Existing document_review_events table is incomplete; missing columns: "
            + ", ".join(sorted(missing_columns))
        )

    foreign_keys = inspector.get_foreign_keys("document_review_events")
    actual_foreign_keys = {
        (column, item.get("referred_table"))
        for item in foreign_keys
        for column in (item.get("constrained_columns") or [])
    }
    missing_foreign_keys = _REQUIRED_FOREIGN_KEYS - actual_foreign_keys
    if missing_foreign_keys:
        raise RuntimeError(
            "Existing document_review_events table is missing required foreign keys: "
            + ", ".join(f"{column}->{table}" for column, table in sorted(missing_foreign_keys))
        )

    unique_items = (
        inspector.get_unique_constraints("document_review_events")
        + inspector.get_indexes("document_review_events")
    )
    has_version_uniqueness = any(
        item.get("name") == "uq_document_review_candidate_version"
        and item.get("column_names") == ["analysis_id", "candidate_index", "decision_version"]
        and item.get("unique", True)
        for item in unique_items
    )
    if not has_version_uniqueness:
        raise RuntimeError("document_review_events is missing candidate-version uniqueness")


def _ensure_index(bind, name: str, columns: list[str]) -> None:
    inspector = inspect(bind)
    indexes = {item.get("name") for item in inspector.get_indexes("document_review_events")}
    constraints = {item.get("name") for item in inspector.get_unique_constraints("document_review_events")}
    if name not in indexes and name not in constraints:
        op.create_index(name, "document_review_events", columns)


def upgrade() -> None:
    bind = op.get_bind()
    _create_table(bind)
    _verify_contract(bind)

    _ensure_index(bind, "ix_document_review_events_analysis_id", ["analysis_id"])
    _ensure_index(bind, "ix_document_review_events_person_id", ["person_id"])
    _ensure_index(bind, "ix_document_review_events_caregiver_id", ["caregiver_id"])
    _ensure_index(
        bind,
        "ix_document_review_events_person_analysis",
        ["person_id", "analysis_id", "created_at"],
    )

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_document_review_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'document review events are append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute(
            "DROP TRIGGER IF EXISTS trg_document_review_events_append_only "
            "ON document_review_events"
        )
        op.execute("""
            CREATE TRIGGER trg_document_review_events_append_only
            BEFORE UPDATE OR DELETE ON document_review_events
            FOR EACH ROW EXECUTE FUNCTION prevent_document_review_event_mutation()
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_document_review_events_no_update
            BEFORE UPDATE ON document_review_events
            BEGIN SELECT RAISE(ABORT, 'document review events are append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_document_review_events_no_delete
            BEFORE DELETE ON document_review_events
            BEGIN SELECT RAISE(ABORT, 'document review events are append-only'); END
        """)


def downgrade() -> None:
    raise RuntimeError(
        "This schema recovery is intentionally forward-only; document review history must not be discarded."
    )
