"""Create review-only Qwen capture analysis storage."""
from alembic import op
import sqlalchemy as sa

revision = "20261022_0004"
down_revision = "20261021_0003"
branch_labels = None
depends_on = None


def upgrade():
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
    op.create_index("ix_capture_analyses_person_id", "capture_analyses", ["person_id"])
    op.create_index("ix_capture_analyses_status", "capture_analyses", ["status"])
    op.create_index("ix_capture_analyses_person_created", "capture_analyses", ["person_id", "created_at"])


def downgrade():
    op.drop_index("ix_capture_analyses_person_created", table_name="capture_analyses")
    op.drop_index("ix_capture_analyses_status", table_name="capture_analyses")
    op.drop_index("ix_capture_analyses_person_id", table_name="capture_analyses")
    op.drop_table("capture_analyses")
