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
    """Create collaboration tables idempotently after ORM bootstrap revisions.

    The preceding schema bootstrap may already have created these tables via
    foreign-key dependency sorting. Use checkfirst for each registered model
    table so a fresh database and a partially bootstrapped database both
    traverse the migration graph without duplicate-table errors.
    """
    bind = op.get_bind()
    from app.core.database import Base

    names = (
        "community_posts",
        "community_reactions",
        "conversation_sessions",
        "conversation_messages",
    )
    missing_metadata = [name for name in names if name not in Base.metadata.tables]
    if missing_metadata:
        raise RuntimeError(
            "Collaboration models are not registered: " + ", ".join(missing_metadata)
        )
    tables = [Base.metadata.tables[name] for name in names]
    Base.metadata.create_all(bind=bind, tables=tables, checkfirst=True)


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
