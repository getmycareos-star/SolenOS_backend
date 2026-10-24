"""Reconcile auth/Qwen ORM schema for databases already stamped at the Task 2 head.

Fresh databases traverse the earlier auth revisions. Existing installations that
already applied the old Task 2 chain will not replay newly inserted ancestors;
this idempotent forward migration brings those databases onto the same schema.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "t2_auth_reconcile_20261023"
down_revision = "t2_caregiver_schema_reconcile_20261024"
branch_labels = None
depends_on = None

TABLES = (
    "auth_accounts",
    "auth_sessions",
    "auth_account_person_access",
    "auth_caregiver_account_links",
    "auth_caregiver_invitations",
    "auth_caregiver_profile_account_links",
    "auth_caregiver_profile_invitations",
    "daily_captures",
    "evidence_files",
    "capture_analyses",
    "community_posts",
    "community_reactions",
    "conversation_sessions",
    "conversation_messages",
)


def upgrade():
    from app.core.database import Base
    import app.models.auth_identity  # noqa: F401
    import app.models.capture  # noqa: F401
    import app.models.capture_analysis  # noqa: F401
    import app.models.collaboration  # noqa: F401

    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in TABLES if name in Base.metadata.tables]
    Base.metadata.create_all(bind=bind, tables=tables, checkfirst=True)

    inspector = inspect(bind)
    if inspector.has_table("auth_accounts"):
        columns = {column["name"] for column in inspector.get_columns("auth_accounts")}
        if "is_anonymous" not in columns:
            op.add_column(
                "auth_accounts",
                sa.Column("is_anonymous", sa.Boolean(), nullable=False, server_default=sa.false()),
            )

    # create_all skips indexes for tables that already exist. Ensure missing
    # auth/capture/collaboration indexes are present without recreating existing ones.
    inspector = inspect(bind)
    for table in tables:
        if inspector.has_table(table.name):
            for index in table.indexes:
                index.create(bind=bind, checkfirst=True)


def downgrade():
    # Preserve user/account data. This reconciliation is intentionally forward-only.
    pass
