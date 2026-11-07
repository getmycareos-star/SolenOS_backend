"""Persist append-only caregiver assignment history for registered actions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261106_01_action_assign"
down_revision = "20261105_02_appt_rev_idem"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns("action_lifecycle_audit")}
    additions = (
        ("previous_responsible_caregiver_id", sa.String(255)),
        ("new_responsible_caregiver_id", sa.String(255)),
        ("handoff_id", sa.String(36)),
        ("assignment_idempotency_key", sa.String(128)),
        ("assignment_request_sha256", sa.String(64)),
    )
    for name, column_type in additions:
        if name not in columns:
            op.add_column("action_lifecycle_audit", sa.Column(name, column_type, nullable=True))
            columns.add(name)
    indexes = {item["name"] for item in inspect(bind).get_indexes("action_lifecycle_audit")}
    constraints = {item.get("name") for item in inspect(bind).get_unique_constraints("action_lifecycle_audit")}
    index_name = "uq_action_lifecycle_audit_action_assignment_key"
    if index_name not in indexes | constraints:
        op.create_index(index_name, "action_lifecycle_audit", ["action_id", "assignment_idempotency_key"], unique=True)
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_action_lifecycle_audit_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'action lifecycle audit is append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("DROP TRIGGER IF EXISTS trg_action_lifecycle_audit_immutable ON action_lifecycle_audit")
        op.execute("""
            CREATE TRIGGER trg_action_lifecycle_audit_immutable
            BEFORE UPDATE OR DELETE ON action_lifecycle_audit
            FOR EACH ROW EXECUTE FUNCTION prevent_action_lifecycle_audit_mutation()
        """)


def downgrade():
    raise RuntimeError("Caregiver assignment history is append-only and cannot be removed automatically.")
