"""Create persistent open-loop records, audit events, and dependencies.

Revision ID: 20261029_18_open_loops
Revises: 20261029_17_pattern_metrics
"""
from alembic import op
import sqlalchemy as sa

revision = "20261029_18_open_loops"
down_revision = "20261029_17_pattern_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "open_loop_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
        sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("loop_type", sa.String(40), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="open"),
        sa.Column("outcome_status", sa.String(32), nullable=False, server_default="outcome_unknown"),
        sa.Column("owner_role", sa.String(100), nullable=True),
        sa.Column("owner_label", sa.String(200), nullable=True),
        sa.Column("source_type", sa.String(80), nullable=True),
        sa.Column("source_id", sa.String(160), nullable=True),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("uncertainty", sa.String(40), nullable=False, server_default="unknown"),
        sa.Column("missing_information", sa.JSON(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=True),
        sa.Column("recipient_label", sa.String(200), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_evidence", sa.JSON(), nullable=False),
        sa.Column("action_evidence", sa.JSON(), nullable=False),
        sa.Column("outcome_evidence", sa.JSON(), nullable=False),
        sa.Column("verification_rule", sa.Text(), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_source", sa.String(40), nullable=True),
        sa.Column("deadline_timezone", sa.String(80), nullable=True),
        sa.Column("consequence_of_delay", sa.String(40), nullable=True),
        sa.Column("priority", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("priority_reasons", sa.JSON(), nullable=False),
        sa.Column("deferred_reason", sa.Text(), nullable=True),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("inactive_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closure_evidence", sa.JSON(), nullable=False),
        sa.Column("closure_result", sa.String(40), nullable=True),
        sa.Column("closure_actor", sa.String(160), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("recurrence_of_id", sa.String(), sa.ForeignKey("open_loop_records.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_open_loop_records_person_id", "open_loop_records", ["person_id"])
    op.create_index("ix_open_loop_records_caregiver_id", "open_loop_records", ["caregiver_id"])
    op.create_index("ix_open_loop_scope_status", "open_loop_records", ["person_id", "caregiver_id", "status"])
    op.create_index("ix_open_loop_scope_deadline", "open_loop_records", ["person_id", "caregiver_id", "deadline_at"])
    op.create_index("ix_open_loop_source_fingerprint", "open_loop_records", ["person_id", "source_fingerprint"])

    op.create_table(
        "open_loop_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("loop_id", sa.String(), sa.ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
        sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=True),
        sa.Column("after_state", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=True),
        sa.Column("actor", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("person_id", "idempotency_key", name="uq_open_loop_event_idempotency"),
    )
    op.create_index("ix_open_loop_events_loop_id", "open_loop_events", ["loop_id"])
    op.create_index("ix_open_loop_events_person_id", "open_loop_events", ["person_id"])
    op.create_index("ix_open_loop_events_caregiver_id", "open_loop_events", ["caregiver_id"])
    op.create_index("ix_open_loop_events_record_created", "open_loop_events", ["loop_id", "created_at"])

    op.create_table(
        "open_loop_dependencies",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
        sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("prerequisite_id", sa.String(), sa.ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dependent_id", sa.String(), sa.ForeignKey("open_loop_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mandatory", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("prerequisite_id", "dependent_id", name="uq_open_loop_dependency_pair"),
    )
    op.create_index("ix_open_loop_dependencies_person_id", "open_loop_dependencies", ["person_id"])
    op.create_index("ix_open_loop_dependencies_caregiver_id", "open_loop_dependencies", ["caregiver_id"])
    op.create_index("ix_open_loop_dependency_dependent", "open_loop_dependencies", ["dependent_id"])


def downgrade() -> None:
    op.drop_table("open_loop_dependencies")
    op.drop_table("open_loop_events")
    op.drop_table("open_loop_records")
