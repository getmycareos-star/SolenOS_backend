#!/usr/bin/env python3
"""Read-only production PostgreSQL schema and Alembic contract verifier.

Run from the repository root:
    python backend/scripts/verify_live_postgres_schema.py

This command performs metadata/catalogue reads only. It never creates, alters,
drops, stamps, or migrates production objects and never reads care-row contents.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sqlalchemy import inspect, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.core.database import Base, database_schema_differences, engine


def _table_inventory(inspector, table_name: str) -> dict:
    """Return structural metadata only; never select application data."""
    return {
        "columns": [
            {
                "name": column["name"],
                "type": str(column["type"]),
                "nullable": bool(column.get("nullable", True)),
                "default": column.get("default"),
                "autoincrement": column.get("autoincrement"),
                "comment": column.get("comment"),
            }
            for column in inspector.get_columns(table_name, schema="public")
        ],
        "primary_key": inspector.get_pk_constraint(table_name, schema="public"),
        "foreign_keys": inspector.get_foreign_keys(table_name, schema="public"),
        "unique_constraints": inspector.get_unique_constraints(table_name, schema="public"),
        "check_constraints": inspector.get_check_constraints(table_name, schema="public"),
        "indexes": inspector.get_indexes(table_name, schema="public"),
    }




def _orm_inventory() -> dict:
    """Return the expected SQLAlchemy storage contract for every registered table."""
    result = {}
    for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name):
        result[table.name] = {
            "columns": [
                {
                    "name": column.name,
                    "type": str(column.type),
                    "nullable": bool(column.nullable),
                    "primary_key": bool(column.primary_key),
                    "default": str(column.default.arg) if column.default is not None else None,
                    "server_default": (
                        str(column.server_default.arg)
                        if column.server_default is not None else None
                    ),
                }
                for column in table.columns
            ],
            "primary_key": [column.name for column in table.primary_key.columns],
            "foreign_keys": sorted(
                (
                    element.parent.name,
                    element.column.table.name,
                    element.column.name,
                    element.ondelete,
                )
                for constraint in table.foreign_key_constraints
                for element in constraint.elements
            ),
            "unique_constraints": sorted(
                sorted(column.name for column in constraint.columns)
                for constraint in table.constraints
                if constraint.__class__.__name__ == "UniqueConstraint"
            ),
            "check_constraints": sorted(
                str(constraint.sqltext)
                for constraint in table.constraints
                if constraint.__class__.__name__ == "CheckConstraint"
            ),
            "indexes": sorted(
                {
                    "columns": [expression.name for expression in index.expressions
                                if hasattr(expression, "name")],
                    "unique": bool(index.unique),
                }
                for index in table.indexes
            , key=lambda item: (item["columns"], item["unique"])),
        }
    return result

def main() -> int:
    if engine.dialect.name != "postgresql":
        print(json.dumps({
            "status": "blocked",
            "reason": "A PostgreSQL DATABASE_URL is required. No schema was changed.",
            "dialect": engine.dialect.name,
            "read_only": True,
        }, indent=2))
        return 2

    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(alembic_config)
    revisions = list(script.walk_revisions())
    expected_heads = sorted(script.get_heads())
    inspector = inspect(engine)
    database_tables = sorted(inspector.get_table_names(schema="public"))
    model_tables = sorted(Base.metadata.tables)
    unmanaged_tables = sorted(set(database_tables) - set(model_tables) - {"alembic_version"})
    missing_model_tables = sorted(set(model_tables) - set(database_tables))

    with engine.connect() as connection:
        migration_context = MigrationContext.configure(connection)
        current_heads = sorted(migration_context.get_current_heads())
        drift = database_schema_differences(connection)
        identity = connection.execute(text(
            "SELECT current_database() AS database_name, "
            "current_schema() AS schema_name, "
            "current_setting('server_version') AS server_version"
        )).mappings().one()
        extensions = connection.execute(text(
            "SELECT extname, extversion FROM pg_extension ORDER BY extname"
        )).mappings().all()
        triggers = connection.execute(text(
            "SELECT event_object_table AS table_name, trigger_name, event_manipulation, "
            "action_timing, action_statement "
            "FROM information_schema.triggers "
            "WHERE trigger_schema = 'public' ORDER BY event_object_table, trigger_name"
        )).mappings().all()

    inventory = {
        table: _table_inventory(inspector, table)
        for table in database_tables
    }
    enums = inspector.get_enums(schema="public") if hasattr(inspector, "get_enums") else []
    single_head = len(expected_heads) == 1
    migration_current = current_heads == expected_heads
    schema_current = not drift and not missing_model_tables
    status = "passed" if single_head and migration_current and schema_current else "failed"

    report = {
        "status": status,
        "read_only": True,
        "source_revision": os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GITHUB_SHA"),
        "database": {
            "dialect": engine.dialect.name,
            "identity": dict(identity),
            "current_alembic_heads": current_heads,
            "extensions": [dict(item) for item in extensions],
            "enums": enums,
            "tables": inventory,
            "table_count": len(database_tables),
            "unmanaged_tables_requiring_ownership_review": unmanaged_tables,
            "missing_orm_tables": missing_model_tables,
            "triggers": [dict(item) for item in triggers],
        },
        "orm": {
            "registered_model_count": len(Base.metadata.tables),
            "registered_tables": model_tables,
            "expected_schema": _orm_inventory(),
            "schema_differences": drift,
        },
        "alembic": {
            "revision_count": len(revisions),
            "expected_heads": expected_heads,
            "current_heads_match": migration_current,
            "single_head": single_head,
            "revisions": [
                {
                    "revision": revision.revision,
                    "down_revision": revision.down_revision,
                    "branch_labels": sorted(revision.branch_labels or []),
                    "depends_on": revision.dependencies,
                }
                for revision in revisions
            ],
        },
        "warnings": [
            "Unmanaged tables require explicit ownership review; they are not silently classified as errors.",
            "This report describes schema structure only and does not inspect caregiver data or prove workflow behavior.",
        ],
    }
    print(json.dumps(report, indent=2, default=str))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
