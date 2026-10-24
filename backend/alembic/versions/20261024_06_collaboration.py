"""Add persisted Community and Companion records.

Revision ID: 20261024_06
Revises: 20261024_05
"""
from alembic import op
import sqlalchemy as sa

revision = "20261024_06"
down_revision = "20261024_05"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "community_posts",
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
    op.create_index("ix_community_posts_caregiver_id", "community_posts", ["caregiver_id"])
    op.create_index("ix_community_posts_visibility", "community_posts", ["visibility"])
    op.create_index("ix_community_posts_moderation_status", "community_posts", ["moderation_status"])
    op.create_index("ix_community_posts_created_at", "community_posts", ["created_at"])
    op.create_table(
        "community_reactions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("post_id", sa.String(), sa.ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("reaction_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("post_id", "caregiver_id", name="uq_community_reaction_actor_post"),
    )
    op.create_index("ix_community_reactions_post_id", "community_reactions", ["post_id"])
    op.create_index("ix_community_reactions_caregiver_id", "community_reactions", ["caregiver_id"])
    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
        sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_conversation_sessions_person_id", "conversation_sessions", ["person_id"])
    op.create_index("ix_conversation_sessions_caregiver_id", "conversation_sessions", ["caregiver_id"])
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("guard_verdict", sa.String(), nullable=True),
        sa.Column("safety_note", sa.Text(), nullable=True),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_conversation_messages_session_id", "conversation_messages", ["session_id"])
    op.create_index("ix_conversation_messages_created_at", "conversation_messages", ["created_at"])


def downgrade():
    op.drop_table("conversation_messages")
    op.drop_table("conversation_sessions")
    op.drop_table("community_reactions")
    op.drop_table("community_posts")
