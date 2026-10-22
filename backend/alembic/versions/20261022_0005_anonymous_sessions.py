"""Mark anonymous caregiver sessions explicitly."""
from alembic import op
import sqlalchemy as sa

revision = "20261022_0005"
down_revision = "20261022_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "auth_accounts",
        sa.Column("is_anonymous", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade():
    op.drop_column("auth_accounts", "is_anonymous")
