"""Merge care-circle and Delta Engine migration branches."""
from alembic import op

revision = "20261023_05"
down_revision = ("20261023_01", "20261023_04")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
