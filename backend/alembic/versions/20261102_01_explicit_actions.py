"""Add caregiver-approved registered-action lifecycle and audit history.

Revision ID: 20261102_01_explicit_actions
Revises: 20261030_05_history_merge
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261102_01_explicit_actions"
down_revision = "20261030_05_history_merge"
branch_labels = None
depends_on = None


def _columns(bind, table_name):
    inspector = inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(bind, table_name, column):
    if column.name not in _columns(bind, table_name):
        op.add_column(table_name, column)


def _ensure_unique_constraint(bind, table_name, name, columns):
    # Unique indexes enforce the same invariant and are supported by both
    # PostgreSQL and SQLite; ALTER TABLE ADD CONSTRAINT is not portable to SQLite.
    existing_constraints = {
        item.get("name")
        for item in inspect(bind).get_unique_constraints(table_name)
    }
    existing_indexes = {
        item.get("name")
        for item in inspect(bind).get_indexes(table_name)
    }
    if name not in existing_constraints and name not in existing_indexes:
        op.create_index(name, table_name, columns, unique=True)


def _ensure_index(bind, table_name, name, columns, unique=False):
    existing = {item.get("name") for item in inspect(bind).get_indexes(table_name)}
    if name not in existing:
        op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()

    # The initial durable-schema revision calls Base.metadata.create_all().
    # On a fresh database that already creates the current ORM shape. Existing
    # databases, however, retain their old table shape. Reconcile both paths.
    if not inspect(bind).has_table("actions"):
        raise RuntimeError("The existing actions table must be present before the approval migration")

    action_columns = _columns(bind, "actions")
    if "extraction_confidence" in action_columns:
        extraction = next(
            column for column in inspect(bind).get_columns("actions")
            if column["name"] == "extraction_confidence"
        )
        if not extraction.get("nullable", True):
            op.alter_column(
                "actions",
                "extraction_confidence",
                existing_type=extraction["type"],
                nullable=True,
            )

    action_additions = [
        sa.Column("registration_key", sa.String(length=128), nullable=True),
        sa.Column("suggestion_id", sa.String(length=160), nullable=True),
        sa.Column("action_identity_key", sa.String(length=64), nullable=True),
        sa.Column("registration_request_sha256", sa.String(length=64), nullable=True),
        sa.Column("source_evidence_ids", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responsible_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=True),
        sa.Column("deferred_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    ]
    for column in action_additions:
        _add_column_if_missing(bind, "actions", column)

    _ensure_unique_constraint(
        bind, "actions", "uq_actions_person_registration_key",
        ["person_id", "registration_key"],
    )
    _ensure_unique_constraint(
        bind, "actions", "uq_actions_person_suggestion_id",
        ["person_id", "suggestion_id"],
    )
    _ensure_unique_constraint(
        bind, "actions", "uq_actions_person_identity_key",
        ["person_id", "action_identity_key"],
    )

    if not inspect(bind).has_table("care_reminders"):
        raise RuntimeError("The existing care_reminders table is required for action-linked reminders")
    _add_column_if_missing(
        bind,
        "care_reminders",
        sa.Column("action_id", sa.String(), sa.ForeignKey("actions.id"), nullable=True),
    )
    _ensure_index(bind, "care_reminders", "ix_care_reminders_action_id", ["action_id"], unique=True)

    if not inspect(bind).has_table("action_lifecycle_audit"):
        op.create_table(
            "action_lifecycle_audit",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("action_id", sa.String(), sa.ForeignKey("actions.id"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("actor_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("previous_status", sa.String(), nullable=True),
            sa.Column("new_status", sa.String(), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("evidence_ids", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    _ensure_index(bind, "action_lifecycle_audit", "ix_action_lifecycle_audit_action_id", ["action_id"])
    _ensure_index(bind, "action_lifecycle_audit", "ix_action_lifecycle_audit_person_id", ["person_id"])
    _ensure_index(bind, "action_lifecycle_audit", "ix_action_lifecycle_audit_actor_caregiver_id", ["actor_caregiver_id"])

    # Enforce append-only history at the database boundary as well as through
    # SQLAlchemy ORM guards. IF NOT EXISTS keeps fresh metadata-created tables safe.
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION reject_action_audit_mutation() RETURNS trigger AS $action_audit$
            BEGIN
                RAISE EXCEPTION 'action lifecycle audit is append-only';
            END;
            $action_audit$ LANGUAGE plpgsql
        """)
        op.execute("DROP TRIGGER IF EXISTS trg_action_lifecycle_audit_immutable ON action_lifecycle_audit")
        op.execute("""
            CREATE TRIGGER trg_action_lifecycle_audit_immutable
            BEFORE UPDATE OR DELETE ON action_lifecycle_audit
            FOR EACH ROW EXECUTE FUNCTION reject_action_audit_mutation()
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_action_lifecycle_audit_no_update
            BEFORE UPDATE ON action_lifecycle_audit
            BEGIN SELECT RAISE(ABORT, 'action lifecycle audit is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_action_lifecycle_audit_no_delete
            BEFORE DELETE ON action_lifecycle_audit
            BEGIN SELECT RAISE(ABORT, 'action lifecycle audit is append-only'); END
        """)


def downgrade() -> None:
    raise RuntimeError(
        "Registered-action approval and audit history are durable care records; "
        "downgrade requires a reviewed data-preservation migration."
    )
