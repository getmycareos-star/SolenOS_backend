"""Add retry-safe request identity to append-only evidence reviews.

Revision ID: 20261121_02_evidence_review_idempotency
Revises: 20261121_01_appt_date_optional

A client retry must return the original saved review, not append a duplicate.
Existing historical reviews remain valid with a NULL request key.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261121_02_evidence_review_idem"
down_revision = "20261121_01_appt_date_optional"
branch_labels = None
depends_on = None

TABLE = "evidence_review_records"
INDEX = "uq_evidence_review_person_actor_request"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        raise RuntimeError("Cannot add review idempotency: evidence review table is missing")

    columns = {item["name"] for item in inspector.get_columns(TABLE)}
    if "request_key" not in columns:
        op.add_column(TABLE, sa.Column("request_key", sa.String(length=128), nullable=True))
    if "request_fingerprint" not in columns:
        op.add_column(TABLE, sa.Column("request_fingerprint", sa.String(length=64), nullable=True))

    indexes = {item["name"] for item in inspect(bind).get_indexes(TABLE)}
    if INDEX not in indexes:
        op.create_index(
            INDEX,
            TABLE,
            ["person_id", "reviewed_by_caregiver_id", "request_key"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return
    columns = {item["name"] for item in inspector.get_columns(TABLE)}
    if "request_key" in columns:
        count = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {TABLE} WHERE request_key IS NOT NULL")
        ).scalar_one()
        if count:
            raise RuntimeError("Cannot downgrade while idempotent evidence reviews exist.")
    indexes = {item["name"] for item in inspect(bind).get_indexes(TABLE)}
    if INDEX in indexes:
        op.drop_index(INDEX, table_name=TABLE)
    columns = {item["name"] for item in inspect(bind).get_columns(TABLE)}
    if "request_fingerprint" in columns:
        op.drop_column(TABLE, "request_fingerprint")
    if "request_key" in columns:
        op.drop_column(TABLE, "request_key")
