"""Make appointment workspace creation retry-safe."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261105_01_appt_idempotency"
down_revision = "20261102_02_wf_integrity_merge"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns("appointment_workspace_records")}
    if "idempotency_key" not in columns:
        op.add_column("appointment_workspace_records", sa.Column("idempotency_key", sa.String(128), nullable=True))
    columns = {item["name"] for item in inspect(bind).get_columns("appointment_workspace_records")}
    if "idempotency_request_sha256" not in columns:
        op.add_column("appointment_workspace_records", sa.Column("idempotency_request_sha256", sa.String(64), nullable=True))
    inspector = inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("appointment_workspace_records")}
    constraints = {item.get("name") for item in inspector.get_unique_constraints("appointment_workspace_records")}
    if "uq_appointment_workspace_person_idempotency" not in indexes | constraints:
        op.create_index("uq_appointment_workspace_person_idempotency", "appointment_workspace_records", ["person_id", "idempotency_key"], unique=True)


def downgrade():
    raise RuntimeError("Appointment save idempotency protects durable care history and cannot be removed automatically.")
