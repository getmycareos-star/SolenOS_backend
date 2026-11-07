"""Append-only caregiver review events for document intelligence.

Revision ID: 20261106_01_doc_review_events
Revises: 20261102_02_wf_integrity_merge
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261106_01_doc_review_events"
down_revision = "20261102_02_wf_integrity_merge"
branch_labels = None
depends_on = None


def _ensure_index(bind, table_name: str, name: str, columns: list[str], unique: bool = False) -> None:
    inspector = inspect(bind)
    existing = {item.get("name") for item in inspector.get_indexes(table_name)}
    constraints = {item.get("name") for item in inspector.get_unique_constraints(table_name)}
    if name not in existing and name not in constraints:
        op.create_index(name, table_name, columns, unique=unique)


def _ensure_review_table(bind) -> None:
    inspector = inspect(bind)
    if not inspector.has_table("document_review_events"):
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

    # Earlier baseline/reconciliation revisions call Base.metadata.create_all()
    # with the complete currently registered ORM metadata. On a fresh database
    # that can pre-create this table before this revision is reached. Reconcile
    # that supported path, but fail closed on a partial or incompatible table.
    inspector = inspect(bind)
    required_columns = {
        "id", "analysis_id", "person_id", "caregiver_id", "source_evidence_id",
        "candidate_index", "decision_version", "decision", "rationale",
        "before_state", "after_state", "resulting_assertion_id", "created_at",
    }
    actual_columns = {item["name"] for item in inspector.get_columns("document_review_events")}
    missing_columns = required_columns - actual_columns
    if missing_columns:
        raise RuntimeError(
            "Existing document_review_events table is incomplete; missing columns: "
            + ", ".join(sorted(missing_columns))
        )

    foreign_keys = inspector.get_foreign_keys("document_review_events")
    required_foreign_keys = {
        ("analysis_id", "capture_analyses"),
        ("person_id", "persons"),
        ("caregiver_id", "caregivers"),
        ("source_evidence_id", "evidence"),
        ("resulting_assertion_id", "care_assertions"),
    }
    actual_foreign_keys = {
        (column, item.get("referred_table"))
        for item in foreign_keys
        for column in (item.get("constrained_columns") or [])
    }
    missing_foreign_keys = required_foreign_keys - actual_foreign_keys
    if missing_foreign_keys:
        raise RuntimeError(
            "Existing document_review_events table is missing required foreign keys: "
            + ", ".join(f"{column}->{table}" for column, table in sorted(missing_foreign_keys))
        )

    unique_constraints = inspector.get_unique_constraints("document_review_events")
    unique_indexes = inspector.get_indexes("document_review_events")
    has_candidate_version_uniqueness = any(
        item.get("name") == "uq_document_review_candidate_version"
        and item.get("column_names") == ["analysis_id", "candidate_index", "decision_version"]
        for item in unique_constraints + unique_indexes
        if item.get("unique", True)
    )
    if not has_candidate_version_uniqueness:
        op.create_index(
            "uq_document_review_candidate_version",
            "document_review_events",
            ["analysis_id", "candidate_index", "decision_version"],
            unique=True,
        )

    _ensure_index(bind, "document_review_events", "ix_document_review_events_analysis_id", ["analysis_id"])
    _ensure_index(bind, "document_review_events", "ix_document_review_events_person_id", ["person_id"])
    _ensure_index(bind, "document_review_events", "ix_document_review_events_caregiver_id", ["caregiver_id"])
    _ensure_index(
        bind,
        "document_review_events",
        "ix_document_review_events_person_analysis",
        ["person_id", "analysis_id", "created_at"],
    )


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_review_table(bind)

    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_document_review_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'document review events are append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("DROP TRIGGER IF EXISTS trg_document_review_events_append_only ON document_review_events")
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
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("document_review_events"):
        count = bind.execute(sa.text("SELECT COUNT(*) FROM document_review_events")).scalar_one()
        if count:
            raise RuntimeError(
                "Refusing to drop non-empty append-only document caregiver review history."
            )

        if bind.dialect.name == "postgresql":
            op.execute("DROP TRIGGER IF EXISTS trg_document_review_events_append_only ON document_review_events")
            op.execute("DROP FUNCTION IF EXISTS prevent_document_review_event_mutation()")
        elif bind.dialect.name == "sqlite":
            op.execute("DROP TRIGGER IF EXISTS trg_document_review_events_no_update")
            op.execute("DROP TRIGGER IF EXISTS trg_document_review_events_no_delete")
        op.drop_table("document_review_events")
