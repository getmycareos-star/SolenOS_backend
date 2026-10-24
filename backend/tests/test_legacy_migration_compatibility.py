"""Regression test for the deployed 20261022_03 revision-ID collision.

The production database stamped 20261022_03 after caregiver-schema reconciliation,
while this codebase originally used that same ID for care-continuity tables. This
test reproduces the deployed state and verifies the forward compatibility migration
restores the missing tables without inserting fake people or caregivers.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_legacy_revision_three_is_reconciled_without_synthetic_records(tmp_path):
    backend_dir = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "legacy_revision_three.sqlite"
    database_url = f"sqlite:///{db_path}"
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url

    def alembic(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=backend_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    # Build the schema immediately before the conflicting revision.
    alembic("upgrade", "20261022_02")

    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(caregivers)")}
        # Reproduce the legacy caregiver-reconciliation schema that exists in
        # Supabase at revision 20261022_03. No rows are inserted.
        for name, definition in (
            ("name", "VARCHAR NOT NULL DEFAULT ''"),
            ("relationship", "VARCHAR NOT NULL DEFAULT ''"),
            ("person_id", "VARCHAR"),
            ("timezone", "VARCHAR NOT NULL DEFAULT 'UTC'"),
        ):
            if name not in columns:
                db.execute(f"ALTER TABLE caregivers ADD COLUMN {name} {definition}")
        # The legacy production schema lacks the tables added by the other
        # meaning of revision 20261022_03. Remove any that the current ORM
        # baseline may have created so the forward compatibility migration must
        # restore them after the revision is stamped.
        existing_tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in ("care_action_outcomes", "care_assertions", "care_state_revisions"):
            if table in existing_tables:
                db.execute(f'DROP TABLE "{table}"')
        db.execute("UPDATE alembic_version SET version_num = '20261022_03'")

    # The production DB considers caregiver reconciliation applied, so Alembic
    # must not depend on re-running the care-continuity migration with that ID.
    alembic("upgrade", "head")

    with sqlite3.connect(db_path) as db:
        head = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert head == "20261024_05"
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"care_state_revisions", "care_assertions", "care_action_outcomes"} <= tables
        caregiver_columns = {
            row[1]: row for row in db.execute("PRAGMA table_info(caregivers)")
        }
        assert caregiver_columns["password_hash"][3] == 0, "password_hash should be nullable for anonymous caregivers"
        assert db.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM caregivers").fetchone()[0] == 0
