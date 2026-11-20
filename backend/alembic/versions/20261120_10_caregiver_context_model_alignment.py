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
        existing_labels = tuple(bind.execute(sa.text(
            "SELECT e.enumlabel FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid = t.oid "
            "WHERE t.typname = :type_name ORDER BY e.enumsortorder"
        ), {"type_name": "caregiverproximitycategory"}).scalars())
        if existing_labels != PROXIMITY_VALUES:
            raise RuntimeError(
                "caregiverproximitycategory enum labels differ from the ORM contract: "
                + repr(existing_labels)
            )
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
        ("reported_by_caregiver_id", sa.String(length=36)),
    )
    for name, column_type in additions:
        if name not in columns:
            op.add_column(TABLE, sa.Column(name, column_type, nullable=True))
            columns.add(name)

    # Legacy variants may have introduced nullable ownership/timestamp
    # columns. Never invent a person or timestamp for an orphaned historical
    # row: stop and require explicit reconciliation instead.
    null_person_rows = bind.execute(
        sa.text(f"SELECT count(*) FROM {TABLE} WHERE person_id IS NULL")
    ).scalar_one()
    if null_person_rows:
        raise RuntimeError(
            f"Cannot enforce caregiver_contexts.person_id: {null_person_rows} legacy rows have no care recipient"
        )
    null_created_rows = bind.execute(
        sa.text(f"SELECT count(*) FROM {TABLE} WHERE created_at IS NULL")
    ).scalar_one()
    if null_created_rows:
        raise RuntimeError(
            f"Cannot enforce caregiver_contexts.created_at: {null_created_rows} legacy rows have no storage timestamp"
        )

    orphan_people = bind.execute(sa.text(
        "SELECT count(*) FROM caregiver_contexts c "
        "LEFT JOIN persons p ON p.id = c.person_id WHERE p.id IS NULL"
    )).scalar_one()
    if orphan_people:
        raise RuntimeError(
            f"Cannot add caregiver_contexts.person_id foreign key: {orphan_people} orphan rows exist"
        )
    orphan_caregivers = bind.execute(sa.text(
        "SELECT count(*) FROM caregiver_contexts c "
        "LEFT JOIN caregiver_profiles p ON p.id = c.caregiver_id "
        "WHERE c.caregiver_id IS NOT NULL AND p.id IS NULL"
    )).scalar_one()
    if orphan_caregivers:
        raise RuntimeError(
            f"Cannot add caregiver_contexts.caregiver_id foreign key: {orphan_caregivers} orphan rows exist"
        )

    actual_foreign_keys = {
        (tuple(item.get("constrained_columns") or ()), item.get("referred_table"), tuple(item.get("referred_columns") or ()))
        for item in inspect(bind).get_foreign_keys(TABLE)
    }
    for local_column, remote_table, remote_column, constraint_name in (
        ("person_id", "persons", "id", "fk_caregiver_contexts_person_id_persons"),
        ("caregiver_id", "caregiver_profiles", "id", "fk_caregiver_contexts_caregiver_id_profiles"),
        ("reported_by_caregiver_id", "caregivers", "id", "fk_caregiver_contexts_reported_by_caregiver_id"),
    ):
        key = ((local_column,), remote_table, (remote_column,))
        if key not in actual_foreign_keys:
            if bind.dialect.name == "sqlite":
                with op.batch_alter_table(TABLE) as batch:
                    batch.create_foreign_key(
                        constraint_name,
                        remote_table,
                        [local_column],
                        [remote_column],
                        ondelete=("SET NULL" if local_column == "reported_by_caregiver_id" else None),
                    )
            else:
                op.create_foreign_key(
                    constraint_name,
                    TABLE,
                    remote_table,
                    [local_column],
                    [remote_column],
                    ondelete=("SET NULL" if local_column == "reported_by_caregiver_id" else None),
                )

    for column_name, column_type in (
        ("person_id", sa.String(length=36)),
        ("created_at", sa.DateTime(timezone=True)),
    ):
        column = next(item for item in inspect(bind).get_columns(TABLE) if item["name"] == column_name)
        if bool(column.get("nullable", True)):
            if bind.dialect.name == "sqlite":
                with op.batch_alter_table(TABLE) as batch:
                    batch.alter_column(column_name, existing_type=column_type, nullable=False)
            else:
                op.alter_column(TABLE, column_name, existing_type=column_type, nullable=False)

    # The active ORM permits absent effective-time and optional context
    # attributes for legacy records. Relax historical NOT NULL constraints
    # rather than fabricate values; new API writes still provide timestamps.
    for column_name in (
        "caregiver_id",
        "proximity_category",
        "observation_window_start",
        "observation_window_end",
        "availability_notes",
        "context_metadata",
        "effective_start",
        "effective_end",
    ):
        column = next(item for item in inspect(bind).get_columns(TABLE) if item["name"] == column_name)
        if not bool(column.get("nullable", True)):
            if bind.dialect.name == "sqlite":
                with op.batch_alter_table(TABLE) as batch:
                    batch.alter_column(column_name, existing_type=column["type"], nullable=True)
            else:
                op.alter_column(TABLE, column_name, existing_type=column["type"], nullable=True)

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
        "reported_by_caregiver_id",
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
