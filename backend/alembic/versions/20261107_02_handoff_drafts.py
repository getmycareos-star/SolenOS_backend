"""Add persistent caregiver-reviewed handoff drafts and immutable revisions.

Revision ID: 20261107_02_handoff_drafts
Revises: 20261107_02_doc_companion_merge
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261107_02_handoff_drafts"
down_revision = "20261107_02_doc_companion_merge"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("handoff_drafts"):
        op.create_table(
            "handoff_drafts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("person_id", sa.String(255), nullable=False),
            sa.Column("caregiver_id", sa.String(255), nullable=False),
            sa.Column("request_id", sa.String(255), nullable=False),
            sa.Column("request_fingerprint", sa.String(64), nullable=False),
            sa.Column("purpose", sa.String(64), nullable=False),
            sa.Column("audience", sa.String(32), nullable=False),
            sa.Column("window_days", sa.Integer(), nullable=False),
            sa.Column("structured_content", sa.JSON(), nullable=False),
            sa.Column("source_snapshot", sa.JSON(), nullable=False),
            sa.Column("generation_metadata", sa.JSON(), nullable=False),
            sa.Column("generation_status", sa.String(48), nullable=False, server_default="not_generated"),
            sa.Column("validation_status", sa.String(48), nullable=False, server_default="not_assessed"),
            sa.Column("review_status", sa.String(48), nullable=False, server_default="pending_review"),
            sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by_caregiver_id", sa.String(255), nullable=True),
            sa.Column("approval_revision", sa.Integer(), nullable=True),
            sa.Column("approval_content_sha256", sa.String(64), nullable=True),
            sa.Column("approval_parameters", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("person_id", "caregiver_id", "request_id", name="uq_handoff_drafts_request"),
        )
    if not inspect(bind).has_table("handoff_draft_revisions"):
        op.create_table(
            "handoff_draft_revisions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("draft_id", sa.String(36), nullable=False),
            sa.Column("person_id", sa.String(255), nullable=False),
            sa.Column("caregiver_id", sa.String(255), nullable=False),
            sa.Column("revision_number", sa.Integer(), nullable=False),
            sa.Column("content", sa.JSON(), nullable=False),
            sa.Column("source_snapshot", sa.JSON(), nullable=False),
            sa.Column("content_sha256", sa.String(64), nullable=False),
            sa.Column("change_type", sa.String(32), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["draft_id"], ["handoff_drafts.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("draft_id", "revision_number", name="uq_handoff_draft_revision_number"),
        )
    indexes = {item["name"] for item in inspect(bind).get_indexes("handoff_drafts")}
    for name, table, columns in (
        ("ix_handoff_drafts_person_id", "handoff_drafts", ["person_id"]),
        ("ix_handoff_drafts_caregiver_id", "handoff_drafts", ["caregiver_id"]),
        ("ix_handoff_drafts_created_at", "handoff_drafts", ["created_at"]),
    ):
        if name not in indexes:
            op.create_index(name, table, columns)
    revision_indexes = {item["name"] for item in inspect(bind).get_indexes("handoff_draft_revisions")}
    for name, column in (
        ("ix_handoff_draft_revisions_draft_id", "draft_id"),
        ("ix_handoff_draft_revisions_person_id", "person_id"),
        ("ix_handoff_draft_revisions_caregiver_id", "caregiver_id"),
        ("ix_handoff_draft_revisions_created_at", "created_at"),
    ):
        if name not in revision_indexes:
            op.create_index(name, "handoff_draft_revisions", [column])


    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION reject_handoff_draft_revision_mutation() RETURNS trigger AS $handoff_revision$
            BEGIN
                RAISE EXCEPTION 'Handoff draft revision history is append-only';
            END;
            $handoff_revision$ LANGUAGE plpgsql
        """)
        op.execute("DROP TRIGGER IF EXISTS trg_handoff_draft_revisions_immutable ON handoff_draft_revisions")
        op.execute("""
            CREATE TRIGGER trg_handoff_draft_revisions_immutable
            BEFORE UPDATE OR DELETE ON handoff_draft_revisions
            FOR EACH ROW EXECUTE FUNCTION reject_handoff_draft_revision_mutation()
        """)
    elif bind.dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_handoff_draft_revisions_no_update
            BEFORE UPDATE ON handoff_draft_revisions
            BEGIN SELECT RAISE(ABORT, 'Handoff draft revision history is append-only'); END
        """)
        op.execute("""
            CREATE TRIGGER IF NOT EXISTS trg_handoff_draft_revisions_no_delete
            BEFORE DELETE ON handoff_draft_revisions
            BEGIN SELECT RAISE(ABORT, 'Handoff draft revision history is append-only'); END
        """)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in ("handoff_draft_revisions", "handoff_drafts"):
        if inspector.has_table(table):
            count = bind.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
            if count:
                raise RuntimeError("Refusing to discard persisted handoff drafts or revision history")
    if bind.dialect.name == "postgresql":
        # PostgreSQL will not drop a trigger function while a table trigger
        # still references it. Remove the immutable-history trigger first.
        if inspector.has_table("handoff_draft_revisions"):
            op.execute(
                "DROP TRIGGER IF EXISTS trg_handoff_draft_revisions_immutable "
                "ON handoff_draft_revisions"
            )
        op.execute("DROP FUNCTION IF EXISTS reject_handoff_draft_revision_mutation()")
    if inspector.has_table("handoff_draft_revisions"):
        op.drop_table("handoff_draft_revisions")
    if inspector.has_table("handoff_drafts"):
        op.drop_table("handoff_drafts")
