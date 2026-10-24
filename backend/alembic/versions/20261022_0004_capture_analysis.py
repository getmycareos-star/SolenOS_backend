"""Create review-only Qwen capture analysis storage."""
from alembic import op
import sqlalchemy as sa

revision = "20261022_0004"
down_revision = "20261021_0003"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("capture_analyses"):
        op.create_table(
            "capture_analyses",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("care_event_id", sa.String(), sa.ForeignKey("care_events.id", ondelete="CASCADE"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("source_evidence_ids", sa.JSON(), nullable=False),
            sa.Column("provider_status", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("model_name", sa.String(length=160), nullable=True),
            sa.Column("candidate_data", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("care_event_id", name="uq_capture_analysis_care_event"),
        )

    # The initial auth migration bootstraps registered model tables. When the
    # CaptureAnalysis model is registered, that bootstrap may create this table
    # before this revision runs. Ensure indexes idempotently in either path.
    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("capture_analyses")}
    expected = [
        ("ix_capture_analyses_person_id", ["person_id"]),
        ("ix_capture_analyses_status", ["status"]),
        ("ix_capture_analyses_person_created", ["person_id", "created_at"]),
    ]
    for name, columns in expected:
        if name not in indexes:
            op.create_index(name, "capture_analyses", columns)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("capture_analyses"):
        return
    indexes = {item["name"] for item in inspector.get_indexes("capture_analyses")}
    for name in (
        "ix_capture_analyses_person_created",
        "ix_capture_analyses_status",
        "ix_capture_analyses_person_id",
    ):
        if name in indexes:
            op.drop_index(name, table_name="capture_analyses")
    op.drop_table("capture_analyses")
