"""Make appointment workspace revisions retry-safe."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261105_02_appt_rev_idem"
down_revision = "20261105_01_appt_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in inspect(bind).get_columns("appointment_workspace_revisions")}
    if "idempotency_key" not in columns:
        op.add_column("appointment_workspace_revisions", sa.Column("idempotency_key", sa.String(128), nullable=True))
    columns = {item["name"] for item in inspect(bind).get_columns("appointment_workspace_revisions")}
    if "idempotency_request_sha256" not in columns:
        op.add_column("appointment_workspace_revisions", sa.Column("idempotency_request_sha256", sa.String(64), nullable=True))
    inspector = inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("appointment_workspace_revisions")}
    constraints = {item.get("name") for item in inspector.get_unique_constraints("appointment_workspace_revisions")}
    if "uq_appointment_workspace_revision_idempotency" not in indexes | constraints:
        op.create_index("uq_appointment_workspace_revision_idempotency", "appointment_workspace_revisions", ["record_id", "idempotency_key"], unique=True)


def downgrade():
def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("appointment_workspace_revisions"):
        return
    columns = {item["name"] for item in inspector.get_columns("appointment_workspace_revisions")}
    if "idempotency_key" in columns:
        count = bind.execute(
            sa.text('SELECT COUNT(*) FROM "appointment_workspace_revisions" WHERE idempotency_key IS NOT NULL')
        ).scalar_one()
        if count:
            raise RuntimeError("Refusing to remove appointment_workspace_revisions retry keys while persisted values are in use.")
    unique_constraints = {item.get("name") for item in inspector.get_unique_constraints("appointment_workspace_revisions")}
    indexes = {item.get("name") for item in inspector.get_indexes("appointment_workspace_revisions")}
    if "uq_appointment_workspace_revision_idempotency" in unique_constraints:
        op.drop_constraint("uq_appointment_workspace_revision_idempotency", "appointment_workspace_revisions", type_="unique")
    elif "uq_appointment_workspace_revision_idempotency" in indexes:
        op.drop_index("uq_appointment_workspace_revision_idempotency", table_name="appointment_workspace_revisions")
    columns = {item["name"] for item in inspect(bind).get_columns("appointment_workspace_revisions")}
    if "idempotency_request_sha256" in columns:
        op.drop_column("appointment_workspace_revisions", "idempotency_request_sha256")
    if "idempotency_key" in columns:
        op.drop_column("appointment_workspace_revisions", "idempotency_key")

