from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261023_08"
down_revision = "20261023_07"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("actions"):
        columns = {c["name"] for c in inspector.get_columns("actions")}
        if "source_authority_verified" not in columns:
            op.add_column("actions", sa.Column("source_authority_verified", sa.Boolean(), nullable=False, server_default="false"))


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("actions"):
        columns = {c["name"] for c in inspector.get_columns("actions")}
        if "source_authority_verified" in columns:
            op.drop_column("actions", "source_authority_verified")
