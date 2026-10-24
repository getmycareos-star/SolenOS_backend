"""Add persisted Community and Companion records.

Revision ID: 20261024_06
Revises: 20261024_05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261024_06"
down_revision = "20261024_05"
branch_labels = None
depends_on = None


def _create_table_if_missing(bind, name, *columns, **kwargs):
    if not inspect(bind).has_table(name):
        op.create_table(name, *columns, **kwargs)


def _create_index_if_missing(bind, name, table, columns, unique=False):
    inspector = inspect(bind)
    if inspector.has_table(table):
        existing = {item["name"] for item in inspector.get_indexes(table)}
        if name not in existing:
            op.create_index(name, table, columns, unique=unique)


def upgrade():
    bind = op.get_bind()
    _create_table_if_missing(
        bind, "evidence_uploads",
        sa.Column("evidence_id", sa.String(), sa.ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    _create_table_if_missing(
        bind, "community_posts",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("author_type", sa.String(), nullable=False, server_default="peer_caregiver"),
        sa.Column("author_label", sa.String(), nullable=False, server_default="Anonymous caregiver"),
        sa.Column("post_type", sa.String(), nullable=False, server_default="experience"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("care_stage", sa.String(), nullable=False, server_default="unspecified"),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("visibility", sa.String(), nullable=False, server_default="public"),
        sa.Column("moderation_status", sa.String(), nullable=False, server_default="published"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, cols in [
        ("ix_community_posts_caregiver_id", ["caregiver_id"]),
        ("ix_community_posts_visibility", ["visibility"]),
        ("ix_community_posts_moderation_status", ["moderation_status"]),
        ("ix_community_posts_created_at", ["created_at"]),
    ]:
        _create_index_if_missing(bind, name, "community_posts", cols)

    _create_table_if_missing(
        bind, "community_reactions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("post_id", sa.String(), sa.ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("reaction_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("post_id", "caregiver_id", name="uq_community_reaction_actor_post"),
    )
    for name, cols in [
        ("ix_community_reactions_post_id", ["post_id"]),
        ("ix_community_reactions_caregiver_id", ["caregiver_id"]),
    ]:
        _create_index_if_missing(bind, name, "community_reactions", cols)

    _create_table_if_missing(
        bind, "conversation_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
        sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, cols in [
        ("ix_conversation_sessions_person_id", ["person_id"]),
        ("ix_conversation_sessions_caregiver_id", ["caregiver_id"]),
    ]:
        _create_index_if_missing(bind, name, "conversation_sessions", cols)

    _create_table_if_missing(
        bind, "conversation_messages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("guard_verdict", sa.String(), nullable=True),
        sa.Column("safety_note", sa.Text(), nullable=True),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for name, cols in [
        ("ix_conversation_messages_session_id", ["session_id"]),
        ("ix_conversation_messages_created_at", ["created_at"]),
    ]:
        _create_index_if_missing(bind, name, "conversation_messages", cols)


def downgrade():
    bind = op.get_bind()
    for table in ("conversation_messages", "conversation_sessions", "community_reactions", "community_posts", "evidence_uploads"):
        if inspect(bind).has_table(table):
            op.drop_table(table)
