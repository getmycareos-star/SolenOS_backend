"""Align the migrated caregiver_contexts table with its active ORM model.

Revision ID: 20261120_10_caregiver_context_model_alignment
Revises: 20261120_09_notification_attempt_indexes

The earlier additive caregiver-context migration preserved a legacy payload
contract. The active CaregiverContext ORM also reads/writes the proximity,
observation-window, metadata and effective-end fields below. Add these fields
without dropping or rewriting the legacy columns or existing rows.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "20261120_10_caregiver_context_model_alignment"
down_revision = "20261120_09_notification_attempt_indexes"
branch_labels = None
depends_on = None

TABLE = "caregiver_contexts"
PROXIMITY_VALUES = ("LIVES_WITH", "LOCAL", "REGIONAL", "REMOTE")


def _column_names(bind):
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        raise RuntimeError("Cannot align caregiver_contexts: table is missing")
    return {column["name"] for column in inspector.get_columns(TABLE)}


def upgrade():
    bind = op.get_bind()
    columns = _column_names(bind)

    if bind.dialect.name == "postgresql":
        enum_type = postgresql.ENUM(
            *PROXIMITY_VALUES,
            name="caregiverproximitycategory",
        )
        enum_type.create(bind, checkfirst=True)
        proximity_column_type = postgresql.ENUM(
            *PROXIMITY_VALUES,
            name="caregiverproximitycategory",
            create_type=False,
        )
    else:
        proximity_column_type = sa.String(length=32)

    additions = (
        ("proximity_category", proximity_column_type),
        ("observation_window_start", sa.String()),
        ("observation_window_end", sa.String()),
        ("availability_notes", sa.Text()),
        ("context_metadata", sa.Text()),
        ("effective_start", sa.DateTime(timezone=True)),
        ("effective_end", sa.DateTime(timezone=True)),
    )
    for name, column_type in additions:
        if name not in columns:
            op.add_column(TABLE, sa.Column(name, column_type, nullable=True))
            columns.add(name)

    existing_index_definitions = {
        (tuple(index.get("column_names") or ()), bool(index.get("unique", False)))
        for index in inspect(bind).get_indexes(TABLE)
    }
    # Match the ORM's explicit single-column indexes as well as the composite
    # lookup index used by the versioned profile API.
    for index_name, index_columns in (
        ("ix_caregiver_contexts_person_id", ["person_id"]),
        ("ix_caregiver_contexts_caregiver_id", ["caregiver_id"]),
        ("ix_caregiver_contexts_person_caregiver_effective_start", ["person_id", "caregiver_id", "effective_start"]),
    ):
        if (tuple(index_columns), False) not in existing_index_definitions:
            op.create_index(index_name, TABLE, index_columns, unique=False)

    required = {
        "id",
        "person_id",
        "caregiver_id",
        "proximity_category",
        "observation_window_start",
        "observation_window_end",
        "availability_notes",
        "context_metadata",
        "effective_start",
        "effective_end",
        "created_at",
    }
    missing = sorted(required - _column_names(bind))
    if missing:
        raise RuntimeError(
            "caregiver_contexts ORM contract is incomplete after additive migration: "
            + ", ".join(missing)
        )


def downgrade():
    # Preserve care context history. Removing these columns would destroy data
    # written by the active CaregiverContext model.
    pass
