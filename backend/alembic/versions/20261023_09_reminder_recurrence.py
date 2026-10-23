from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
revision = "20261023_09"
down_revision = "20261023_08"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("care_reminders"):
        cols = {c["name"] for c in inspect(bind).get_columns("care_reminders")}
        if "recurrence_rule" not in cols:
            op.add_column("care_reminders", sa.Column("recurrence_rule", sa.String(), nullable=True))

def downgrade():
    bind = op.get_bind()
    if inspect(bind).has_table("care_reminders"):
        cols = {c["name"] for c in inspect(bind).get_columns("care_reminders")}
        if "recurrence_rule" in cols:
            op.drop_column("care_reminders", "recurrence_rule")
