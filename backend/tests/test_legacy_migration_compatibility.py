"""Regression test for the deployed 20261022_03 revision-ID collision.

The production database was stamped 20261022_03 after caregiver-schema
reconciliation, while this codebase originally used that ID for care-continuity
tables. This reproduces the legacy stamp and verifies the forward compatibility
migration restores missing tables without inserting fake people or caregivers.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_legacy_revision_three_is_reconciled_without_synthetic_records(tmp_path):
    backend_dir = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "legacy_revision_three.sqlite"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

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
        # Reproduce the caregiver-reconciliation schema already present in
        # production. No caregiver or person rows are inserted.
        for name, definition in (
            ("name", "VARCHAR NOT NULL DEFAULT ''"),
            ("relationship", "VARCHAR NOT NULL DEFAULT ''"),
            ("person_id", "VARCHAR"),
            ("timezone", "VARCHAR NOT NULL DEFAULT 'UTC'"),
        ):
            if name not in columns:
                db.execute(f"ALTER TABLE caregivers ADD COLUMN {name} {definition}")

        # Some current baseline versions may create these ORM tables before
        # the revision that normally creates them; remove them to simulate the
        # deployed database where the legacy revision skipped that migration.
        existing_tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in ("care_action_outcomes", "care_assertions", "care_state_revisions"):
            if table in existing_tables:
                db.execute(f'DROP TABLE "{table}"')
        db.execute("UPDATE alembic_version SET version_num = '20261022_03'")

    # The compatibility migration must repair the missing schema even though
    # Alembic correctly treats the legacy 20261022_03 stamp as already applied.
    alembic("upgrade", "head")

    with sqlite3.connect(db_path) as db:
        head = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert head == "20261024_05"
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"care_state_revisions", "care_assertions", "care_action_outcomes"} <= tables
        caregiver_columns = {
            row[1]: row for row in db.execute("PRAGMA table_info(caregivers)")
        }
        assert caregiver_columns["password_hash"][3] == 0
        assert db.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM caregivers").fetchone()[0] == 0
