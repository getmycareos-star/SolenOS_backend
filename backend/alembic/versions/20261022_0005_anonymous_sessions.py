"""Mark anonymous caregiver sessions explicitly.

The auth bootstrap revision may create this column from ORM metadata on a fresh
PostgreSQL database. Keep this revision safe for both fresh and pre-existing
schemas by adding the column only when it is absent.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261022_0005"
down_revision = "20261022_0004"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("auth_accounts"):
        # On a fresh database the later durable-schema baseline creates the
        # complete auth_accounts table from ORM metadata, including is_anonymous.
        # Do not fail the migration chain before that bootstrap can run.
        return

    columns = {column["name"] for column in inspector.get_columns("auth_accounts")}
    if "is_anonymous" not in columns:
        op.add_column(
            "auth_accounts",
            sa.Column(
                "is_anonymous",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("auth_accounts"):
        return

    columns = {column["name"] for column in inspector.get_columns("auth_accounts")}
    if "is_anonymous" in columns:
        op.drop_column("auth_accounts", "is_anonymous")
