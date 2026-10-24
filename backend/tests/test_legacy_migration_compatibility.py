"""Regression test for upgrading a database stamped at the legacy 20261022_03 revision.

The production database was stamped 20261022_03 after caregiver-schema
reconciliation, while care-continuity tables were absent. This reproduces that
legacy stamp and verifies forward migrations restore the missing tables without
inserting fake people or caregivers.
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

    # Build the schema immediately before the legacy caregiver revision.
    alembic("upgrade", "20261022_02")

    with sqlite3.connect(db_path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(caregivers)")}
        # Reproduce the legacy caregiver-reconciliation columns already
        # present in production. PostgreSQL-specific nullability changes for
        # password_hash/display_name are tested by the PostgreSQL migration job
        # because SQLite cannot faithfully exercise ALTER COLUMN nullability.
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
        # deployed database where continuity tables were still absent.
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
        assert head == "20261024_11"
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"care_state_revisions", "care_assertions", "care_action_outcomes", "community_posts", "community_reactions", "conversation_sessions", "conversation_messages", "evidence_uploads", "share_intakes", "caregiver_consents", "care_notifications", "care_notification_preferences"} <= tables
        assert db.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM caregivers").fetchone()[0] == 0
