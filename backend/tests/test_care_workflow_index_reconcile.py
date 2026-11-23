from __future__ import annotations

import importlib.util
from pathlib import Path


def _migration():
    backend_dir = Path(__file__).resolve().parents[1]
    migration_path = (
        backend_dir
        / "alembic"
        / "versions"
        / "20261123_01_care_workflow_index_reconcile.py"
    )
    spec = importlib.util.spec_from_file_location(
        "care_workflow_index_reconcile", migration_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_downgrade_recognizes_only_names_owned_by_this_migration():
    migration = _migration()
    table = "handoff_records"
    column = "expires_at"
    base = migration._index_name(table, column)

    assert migration._is_migration_owned_index(table, column, base)
    assert migration._is_migration_owned_index(table, column, base + "_1")
    assert migration._is_migration_owned_index(table, column, base + "_27")
    assert not migration._is_migration_owned_index(
        table, column, "ix_solenos_schema_preexisting"
    )
    assert not migration._is_migration_owned_index(
        table, column, base + "_other"
    )
    assert not migration._is_migration_owned_index(
        "handoff_recipients", column, base
    )


def test_downgrade_does_not_claim_a_preexisting_schema_index():
    migration = _migration()
    table = "notification_read_receipts"
    column = "reader_id"

    preexisting_name = "ix_solenos_schema_legacy_reader_lookup"
    assert not migration._is_migration_owned_index(
        table, column, preexisting_name
    )
