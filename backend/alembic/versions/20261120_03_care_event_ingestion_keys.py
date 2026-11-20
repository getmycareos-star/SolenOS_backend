"""Persist idempotency on canonical care events, including document-only captures.

Revision ID: 20261120_03_care_event_ingestion_keys
Revises: 20261120_02_document_object_storage
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261120_03_care_event_ingestion_keys"
down_revision = "20261120_02_document_object_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_events"):
        raise RuntimeError("care_events must exist before adding capture idempotency")

    columns = {column["name"] for column in inspector.get_columns("care_events")}
    if "ingestion_key" not in columns:
        op.add_column(
            "care_events",
            sa.Column("ingestion_key", sa.String(length=128), nullable=True),
        )
    if "ingestion_request_sha256" not in columns:
        op.add_column(
            "care_events",
            sa.Column("ingestion_request_sha256", sa.String(length=64), nullable=True),
        )

    constraints = {
        item.get("name") for item in inspect(bind).get_unique_constraints("care_events")
    }
    indexes = {item.get("name") for item in inspect(bind).get_indexes("care_events")}
    if "uq_care_events_person_ingestion_key" not in constraints | indexes:
        duplicates = bind.execute(
            sa.text(
                """
                SELECT person_id, ingestion_key, COUNT(*)
                FROM care_events
                WHERE ingestion_key IS NOT NULL
                GROUP BY person_id, ingestion_key
                HAVING COUNT(*) > 1
                LIMIT 1
                """
            )
        ).first()
        if duplicates:
            raise RuntimeError(
                "Duplicate care-event ingestion keys exist; resolve them before adding uniqueness"
            )
        op.create_unique_constraint(
            "uq_care_events_person_ingestion_key",
            "care_events",
            ["person_id", "ingestion_key"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("care_events"):
        return

    columns = {column["name"] for column in inspector.get_columns("care_events")}
    if "ingestion_key" in columns:
        count = bind.execute(
            sa.text("SELECT COUNT(*) FROM care_events WHERE ingestion_key IS NOT NULL")
        ).scalar_one()
        if count:
            raise RuntimeError(
                "Refusing to remove active care-event idempotency keys; "
                "preserve these keys before downgrading"
            )

    constraints = {
        item.get("name") for item in inspect(bind).get_unique_constraints("care_events")
    }
    if "uq_care_events_person_ingestion_key" in constraints:
        op.drop_constraint(
            "uq_care_events_person_ingestion_key",
            "care_events",
            type_="unique",
        )

    columns = {column["name"] for column in inspect(bind).get_columns("care_events")}
    if "ingestion_request_sha256" in columns:
        op.drop_column("care_events", "ingestion_request_sha256")
    if "ingestion_key" in columns:
        op.drop_column("care_events", "ingestion_key")
