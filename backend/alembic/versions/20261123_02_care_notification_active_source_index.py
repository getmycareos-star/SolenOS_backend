"""Enforce one active source notification per caregiver and care event.

Revision ID: 20261123_02_care_notification_active_source_index
Revises: 20261123_01_care_workflow_index_reconcile

The ORM declares a partial unique index for active care-change and
care-attention notifications. The previous reconciliation migration added
ordinary lookup indexes but omitted this write-integrity constraint.
Existing duplicate rows are reported and preserved; the migration never
chooses a notification to delete or merge.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261123_02_care_notification_active_source_index"
down_revision = "20261123_01_care_workflow_index_reconcile"
branch_labels = None
depends_on = None

TABLE = "care_notifications"
INDEX_NAME = "uq_care_notifications_active_source"
COLUMNS = ("person_id", "caregiver_id", "category", "source_event_id")
PREDICATE = "status = 'active' AND category IN ('care_change', 'care_attention') AND source_event_id IS NOT NULL"


def _normalize_predicate(value):
    if value is None:
        return None
    normalized = " ".join(str(value).strip().split())
    while normalized.startswith("(") and normalized.endswith(")"):
        depth = 0
        encloses_all = True
        for position, character in enumerate(normalized):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and position != len(normalized) - 1:
                    encloses_all = False
                    break
        if not encloses_all or depth != 0:
            break
        normalized = normalized[1:-1].strip()
    return normalized or None


def _postgres_index_is_valid(bind, index_name: str) -> bool:
    return bool(bind.execute(text("""
        SELECT index_data.indisvalid AND index_data.indisready AND index_data.indislive
        FROM pg_index AS index_data
        JOIN pg_class AS index_class ON index_class.oid = index_data.indexrelid
        JOIN pg_namespace AS index_schema ON index_schema.oid = index_class.relnamespace
        WHERE index_schema.nspname = current_schema()
          AND index_class.relname = :index_name
    """), {"index_name": index_name}).scalar())


def _has_exact_index(bind) -> bool:
    expected_predicate = _normalize_predicate(PREDICATE)
    for index in inspect(bind).get_indexes(TABLE):
        options = index.get("dialect_options") or {}
        predicate = (
            options.get("postgresql_where")
            or options.get("sqlite_where")
            or index.get("filter_definition")
        )
        if (
            tuple(index.get("column_names") or ()) == COLUMNS
            and bool(index.get("unique", False))
            and _normalize_predicate(predicate) == expected_predicate
        ):
            if bind.dialect.name != "postgresql":
                return True
            if _postgres_index_is_valid(bind, str(index.get("name") or "")):
                return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        raise RuntimeError("Cannot enforce active source notification uniqueness: care_notifications is missing")
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    missing = set(COLUMNS) - columns
    if missing:
        raise RuntimeError(
            "Cannot enforce active source notification uniqueness; missing columns: "
            + ", ".join(sorted(missing))
        )

    for index in inspector.get_indexes(TABLE):
        if index.get("name") == INDEX_NAME and not (
            tuple(index.get("column_names") or ()) == COLUMNS
            and bool(index.get("unique", False))
            and _normalize_predicate(
                (index.get("dialect_options") or {}).get("postgresql_where")
                or (index.get("dialect_options") or {}).get("sqlite_where")
                or index.get("filter_definition")
            ) == _normalize_predicate(PREDICATE)
        ):
            raise RuntimeError(
                f"Cannot safely create {INDEX_NAME}: an index with that name has a different definition"
            )

    if _has_exact_index(bind):
        return

    if bind.dialect.name == "postgresql":
        same_name = next(
            (index for index in inspector.get_indexes(TABLE) if index.get("name") == INDEX_NAME),
            None,
        )
        if same_name is not None and not _postgres_index_is_valid(bind, INDEX_NAME):
            with op.get_context().autocommit_block():
                op.execute(text('DROP INDEX CONCURRENTLY IF EXISTS "' + INDEX_NAME + '"'))

    duplicates = bind.execute(text("""
        SELECT person_id, caregiver_id, category, source_event_id, COUNT(*) AS row_count
        FROM care_notifications
        WHERE status = 'active'
          AND category IN ('care_change', 'care_attention')
          AND source_event_id IS NOT NULL
        GROUP BY person_id, caregiver_id, category, source_event_id
        HAVING COUNT(*) > 1
        ORDER BY COUNT(*) DESC
        LIMIT 5
    """)).mappings().all()
    if duplicates:
        raise RuntimeError(
            "Cannot enforce active source notification uniqueness: duplicate active rows exist. "
            "No rows were changed; reconcile these groups explicitly: "
            + repr([dict(row) for row in duplicates])
        )

    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(text(
                'CREATE UNIQUE INDEX CONCURRENTLY "' + INDEX_NAME + '" '
                'ON "' + TABLE + '" ("person_id", "caregiver_id", "category", "source_event_id") '
                'WHERE ' + PREDICATE
            ))
    else:
        op.create_index(
            INDEX_NAME,
            TABLE,
            list(COLUMNS),
            unique=True,
            sqlite_where=text(PREDICATE),
        )

    if not _has_exact_index(bind):
        raise RuntimeError("The active source notification unique index was not created as declared")


def downgrade() -> None:
    bind = op.get_bind()
    names = {index.get("name") for index in inspect(bind).get_indexes(TABLE)} if inspect(bind).has_table(TABLE) else set()
    if INDEX_NAME not in names:
        return
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(text('DROP INDEX CONCURRENTLY IF EXISTS "' + INDEX_NAME + '"'))
    else:
        op.drop_index(INDEX_NAME, table_name=TABLE)
