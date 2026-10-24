"""Fail-closed caregiver schema guard before auth bootstrap."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "t0_caregiver_schema_guard_20261024"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregivers"):
        return
    columns = {column["name"] for column in inspector.get_columns("caregivers")}
    missing = sorted({"name", "relationship", "person_id", "timezone"} - columns)
    if not missing:
        return
    count = bind.execute(text("SELECT COUNT(*) FROM caregivers")).scalar_one()
    if count:
        raise RuntimeError(
            "Caregiver schema repair needs reviewed identity mapping; "
            f"{count} existing rows and missing columns {missing}. No DDL applied."
        )
    additions = {
        "name": sa.Column("name", sa.String(), nullable=False),
        "relationship": sa.Column("relationship", sa.String(), nullable=False),
        "person_id": sa.Column(
            "person_id", sa.String(),
            sa.ForeignKey("persons.id", name="fk_caregivers_person_id_persons"),
            nullable=False,
        ),
        "timezone": sa.Column("timezone", sa.String(), nullable=False, server_default="UTC"),
    }
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("caregivers", recreate="always") as batch:
            for name in missing:
                batch.add_column(additions[name])
    else:
        for name in missing:
            op.add_column("caregivers", additions[name])


def downgrade():
    pass
