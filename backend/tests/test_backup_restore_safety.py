"""Safety tests for the destructive-safe backup/restore runner."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_backup_restore.py"
SPEC = importlib.util.spec_from_file_location("solenos_backup_restore", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_accepts_distinct_synthetic_postgres_databases():
    MODULE._validate_database_urls(
        "postgresql+psycopg2://test:test@localhost:5432/solenos_backup_test",
        "postgresql+psycopg2://test:test@localhost:5432/solenos_recovery_test",
    )


@pytest.mark.parametrize(
    ("source", "restore"),
    [
        (
            "postgresql://test:test@localhost:5432/solenos_backup_test",
            "postgresql://test:test@localhost:5432/solenos_backup_test",
        ),
        (
            "postgresql://test:test@localhost:5432/solenos_backup_test",
            "postgresql://other:other@localhost:5432/solenos_backup_test",
        ),
        (
            "postgresql://test:test@localhost:5432/solenos_prod",
            "postgresql://test:test@localhost:5432/solenos_recovery_test",
        ),
        (
            "sqlite:///solenos_backup_test.db",
            "sqlite:///solenos_recovery_test.db",
        ),
    ],
)
def test_rejects_unsafe_or_non_postgres_database_pairs(source, restore):
    with pytest.raises(ValueError):
        MODULE._validate_database_urls(source, restore)


def test_converts_sqlalchemy_driver_url_for_postgres_cli():
    value = MODULE._libpq_url(
        "postgresql+psycopg2://test:secret@localhost:5432/solenos_backup_test"
    )
    assert value.startswith("postgresql://")
    assert "+psycopg2" not in value
    assert "solenos_backup_test" in value
