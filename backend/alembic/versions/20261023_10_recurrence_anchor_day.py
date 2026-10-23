from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261023_10"
down_revision = "20261023_09"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("care_reminders"):
        columns = {column["name"] for column in inspector.get_columns("care_reminders")}
        if "recurrence_anchor_day" not in columns:
            op.add_column("care_reminders", sa.Column("recurrence_anchor_day", sa.Integer(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("care_reminders"):
        columns = {column["name"] for column in inspector.get_columns("care_reminders")}
        if "recurrence_anchor_day" in columns:
            op.drop_column("care_reminders", "recurrence_anchor_day")
