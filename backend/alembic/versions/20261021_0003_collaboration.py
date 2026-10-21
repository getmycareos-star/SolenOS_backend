"""Add persistent Community and Companion collaboration records.

Revision ID: 20261021_0003
Revises: 20261021_0002
"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_0003"
down_revision = "20261021_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "community_posts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("author_account_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("post_type", sa.String(length=24), nullable=False),
        sa.Column("visibility", sa.String(length=24), nullable=False),
        sa.Column("care_stage", sa.String(length=24), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["author_account_id"], ["auth_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_community_posts_author_account_id", "community_posts", ["author_account_id"])
    op.create_index("ix_community_posts_status", "community_posts", ["status"])

    op.create_table(
        "community_reactions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("post_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("reaction_type", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["post_id"], ["community_posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["auth_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "account_id", name="uq_community_reaction_post_account"),
    )
    op.create_index("ix_community_reactions_post_id", "community_reactions", ["post_id"])
    op.create_index("ix_community_reactions_account_id", "community_reactions", ["account_id"])

    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("person_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["auth_accounts.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_sessions_account_id", "conversation_sessions", ["account_id"])
    op.create_index("ix_conversation_sessions_person_id", "conversation_sessions", ["person_id"])

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("guard_verdict", sa.String(length=32), nullable=True),
        sa.Column("safety_note", sa.Text(), nullable=True),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["conversation_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["auth_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_messages_session_id", "conversation_messages", ["session_id"])
    op.create_index("ix_conversation_messages_account_id", "conversation_messages", ["account_id"])
    op.create_index("ix_conversation_messages_session_created", "conversation_messages", ["session_id", "created_at"])


def downgrade():
    op.drop_index("ix_conversation_messages_session_created", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_account_id", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_session_id", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversation_sessions_person_id", table_name="conversation_sessions")
    op.drop_index("ix_conversation_sessions_account_id", table_name="conversation_sessions")
    op.drop_table("conversation_sessions")
    op.drop_index("ix_community_reactions_account_id", table_name="community_reactions")
    op.drop_index("ix_community_reactions_post_id", table_name="community_reactions")
    op.drop_table("community_reactions")
    op.drop_index("ix_community_posts_status", table_name="community_posts")
    op.drop_index("ix_community_posts_author_account_id", table_name="community_posts")
    op.drop_table("community_posts")
