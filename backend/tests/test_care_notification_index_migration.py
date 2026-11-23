"""Regression tests for PostgreSQL's canonical rendering of partial-index predicates."""
import importlib.util
from pathlib import Path


def _migration_module():
    backend_dir = Path(__file__).resolve().parents[1]
    migration_path = backend_dir / "alembic" / "versions" / "20261123_02_care_notification_active_source_index.py"
    spec = importlib.util.spec_from_file_location("care_notification_active_source_index", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_postgres_canonical_predicate_matches_declared_active_source_index():
    migration = _migration_module()

    postgres_rendered = (
        "((status = 'active'::text) "
        "AND (category = ANY (ARRAY['care_change'::text, 'care_attention'::text])) "
        "AND (source_event_id IS NOT NULL))"
    )

    assert migration._normalize_predicate(postgres_rendered) == migration._normalize_predicate(
        migration.PREDICATE
    )


def test_partial_index_predicate_comparison_rejects_additional_or_missing_conditions():
    migration = _migration_module()
    expected = migration._normalize_predicate(migration.PREDICATE)

    assert migration._normalize_predicate(
        migration.PREDICATE + " AND deleted_at IS NULL"
    ) != expected
    assert migration._normalize_predicate(
        "status = 'active' AND category IN ('care_change') AND source_event_id IS NOT NULL"
    ) != expected
