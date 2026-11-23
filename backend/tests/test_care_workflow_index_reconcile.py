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



def test_partial_and_unique_indexes_do_not_satisfy_full_lookup_coverage():
    migration = _migration()
    full_index = {"column_names": ["person_id"], "unique": False}
    partial_index = {
        "column_names": ["person_id"],
        "unique": False,
        "dialect_options": {"postgresql_where": "deleted_at IS NULL"},
    }
    filtered_index = {
        "column_names": ["person_id"],
        "unique": False,
        "filter_definition": "is_active = 1",
    }
    unique_index = {"column_names": ["person_id"], "unique": True}

    assert migration._is_usable_lookup_index(full_index, "person_id")
    assert not migration._is_usable_lookup_index(partial_index, "person_id")
    assert not migration._is_usable_lookup_index(filtered_index, "person_id")
    assert not migration._is_usable_lookup_index(unique_index, "person_id")



def test_downgrade_drops_only_the_exact_migration_owned_lookup_index(monkeypatch):
    migration = _migration()
    table = "handoff_records"
    column = "expires_at"
    base = migration._index_name(table, column)
    indexes = [
        {
            "name": "ix_solenos_schema_preexisting",
            "column_names": [column],
            "unique": False,
        },
        {"name": base + "_1", "column_names": [column], "unique": False},
        {"name": base, "column_names": ["different_column"], "unique": False},
        {"name": base + "_legacy", "column_names": [column], "unique": False},
    ]

    class Inspector:
        def has_table(self, name):
            return name == table

        def get_indexes(self, name):
            return indexes if name == table else []

    class Operations:
        def get_bind(self):
            return object()

        def drop_index(self, name, table_name):
            dropped.append((name, table_name))

    dropped = []
    monkeypatch.setattr(migration, "inspect", lambda _bind: Inspector())
    monkeypatch.setattr(migration, "op", Operations())
    monkeypatch.setattr(migration, "INDEXES", {table: (column,)})

    migration.downgrade()

    assert dropped == [(base + "_1", table)]
