"""Persist idempotent, append-only follow-up result receipt and review events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261106_02_action_results"
down_revision = "20261106_01_action_assign"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns("care_action_outcomes")}
    additions = (
        ("idempotency_key", sa.String(128)),
        ("idempotency_request_sha256", sa.String(64)),
        ("related_outcome_id", sa.String(36)),
        ("review_decision", sa.String(60)),
        ("received_at", sa.DateTime(timezone=True)),
        ("received_by_caregiver_id", sa.String(255)),
        ("reviewed_at", sa.DateTime(timezone=True)),
        ("reviewed_by_caregiver_id", sa.String(255)),
    )
    for name, column_type in additions:
        if name not in columns:
            op.add_column("care_action_outcomes", sa.Column(name, column_type, nullable=True))
            columns.add(name)
    indexes = {item["name"] for item in inspect(bind).get_indexes("care_action_outcomes")}
    constraints = {item.get("name") for item in inspect(bind).get_unique_constraints("care_action_outcomes")}
    index_name = "uq_care_action_outcomes_person_action_idempotency"
    if index_name not in indexes | constraints:
        op.create_index(index_name, "care_action_outcomes", ["person_id", "action_id", "idempotency_key"], unique=True)
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_care_action_outcome_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'care action outcome history is append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("DROP TRIGGER IF EXISTS trg_care_action_outcomes_immutable ON care_action_outcomes")
        op.execute("""
            CREATE TRIGGER trg_care_action_outcomes_immutable
            BEFORE UPDATE OR DELETE ON care_action_outcomes
            FOR EACH ROW EXECUTE FUNCTION prevent_care_action_outcome_mutation()
        """)


def downgrade():
    raise RuntimeError("Follow-up result and review history is append-only and cannot be removed automatically.")
