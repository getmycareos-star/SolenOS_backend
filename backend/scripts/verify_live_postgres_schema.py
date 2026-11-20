#!/usr/bin/env python3
"""Read-only production PostgreSQL schema and Alembic contract verifier.

Run from any working directory:
    python backend/scripts/verify_live_postgres_schema.py

The script never creates or alters tables. It compares the live database with
registered SQLAlchemy metadata and the repository's Alembic revision graph.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sqlalchemy import inspect

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.core.database import Base, database_schema_differences, engine


def main() -> int:
    if engine.dialect.name != "postgresql":
        print(json.dumps({
            "status": "blocked",
            "reason": "The verifier requires the live PostgreSQL DATABASE_URL; no schema was changed.",
            "dialect": engine.dialect.name,
        }, indent=2))
        return 2

    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(alembic_config)
    revisions = list(script.walk_revisions())
    expected_heads = sorted(script.get_heads())

    with engine.connect() as connection:
        migration_context = MigrationContext.configure(connection)
        current_heads = sorted(migration_context.get_current_heads())
        drift = database_schema_differences(connection)
        database_tables = sorted(inspect(connection).get_table_names())

    model_tables = sorted(Base.metadata.tables.keys())
    unmanaged_tables = sorted(
        set(database_tables) - set(model_tables) - {"alembic_version"}
    )
    report = {
        "status": "passed" if not drift and current_heads == expected_heads and len(expected_heads) == 1 else "failed",
        "database": {
            "dialect": engine.dialect.name,
            "current_alembic_heads": current_heads,
            "database_tables": database_tables,
            "unmanaged_tables_requiring_ownership_review": unmanaged_tables,
        },
        "orm": {
            "registered_model_count": len(Base.metadata.tables),
            "registered_tables": model_tables,
            "schema_differences": drift,
        },
        "alembic": {
            "revision_count": len(revisions),
            "expected_heads": expected_heads,
            "single_head": len(expected_heads) == 1,
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
        "source_revision": (
            os.getenv("RAILWAY_GIT_COMMIT_SHA")
            or os.getenv("GITHUB_SHA")
            or None
        ),
        "read_only": True,
    }
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
