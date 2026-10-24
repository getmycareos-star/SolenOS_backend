from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_task2_and_care_continuity_migrations_form_one_head():
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    script = ScriptDirectory.from_config(config)

    # PR #21, Task 2, Task 3 handoffs, and Task 4 document assets must form one linear chain.
    assert script.get_heads() == ["t5_proactive_items_20261024"]
    revisions = list(script.walk_revisions())
    revision_ids = [revision.revision for revision in revisions]
    assert len(revision_ids) == len(set(revision_ids))
    assert all(len(revision_id) <= 32 for revision_id in revision_ids)

    down_revisions = {
        revision.revision: revision.down_revision
        for revision in revisions
    }
    assert down_revisions["t0_caregiver_guard_20261024"] is None
    assert down_revisions["20261021_0001"] == "t0_caregiver_guard_20261024"
    assert down_revisions["20261022_01"] == "20261022_0005"
    assert down_revisions["20261022_03"] == "20261022_02"
    assert down_revisions["t2_care_continuity_20261024"] == "20261022_03"
    assert down_revisions["20261023_04"] == "t2_care_continuity_20261024"
    assert down_revisions["t2_completion_20261023"] == "20261023_05"
    assert down_revisions["t2_caregiver_guard_20261024"] == "t2_delivery_attempts_20261023"
    assert down_revisions["t2_auth_reconcile_20261023"] == "t2_caregiver_guard_20261024"
    assert down_revisions["t3_care_handoffs_20261023"] == "t2_auth_reconcile_20261023"
    assert down_revisions["t4_document_assets_20261023"] == "t3_care_handoffs_20261023"
    assert down_revisions["t5_handoff_20261023"] == "t4_document_assets_20261023"
    assert down_revisions["t5_proactive_items_20261024"] == "t5_handoff_20261023"


def test_alembic_root_logger_configuration_supports_upgrade_smoke_test():
    backend_dir = Path(__file__).resolve().parents[1]
    config_text = (backend_dir / "alembic.ini").read_text(encoding="utf-8")
    root_section = config_text.split("[logger_root]", 1)[1].split("[", 1)[0]
    assert "qualname = root" in root_section


def _load_caregiver_schema_migration(filename="t2_caregiver_schema_reconcile_20261024.py"):
    import importlib.util

    backend_dir = Path(__file__).resolve().parents[1]
    path = backend_dir / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location("caregiver_schema_reconcile_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("filename", [
    "t0_caregiver_schema_guard_20261024.py",
    "20261022_03_caregiver_schema_reconciliation.py",
    "t2_caregiver_schema_reconcile_20261024.py",
])
def test_caregiver_schema_reconciliation_adds_columns_only_to_an_empty_legacy_table(filename):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine("sqlite://")
    migration = _load_caregiver_schema_migration(filename)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE persons (id VARCHAR PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE caregivers (id VARCHAR PRIMARY KEY, email VARCHAR)"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            columns = {column["name"] for column in inspect(connection).get_columns("caregivers")}
            assert {"name", "relationship", "person_id", "timezone"} <= columns
    finally:
        engine.dispose()


@pytest.mark.parametrize("filename", [
    "t0_caregiver_schema_guard_20261024.py",
    "20261022_03_caregiver_schema_reconciliation.py",
    "t2_caregiver_schema_reconcile_20261024.py",
])
def test_caregiver_schema_reconciliation_refuses_to_guess_existing_identity_mapping(filename):
    import pytest
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine("sqlite://")
    migration = _load_caregiver_schema_migration(filename)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE persons (id VARCHAR PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE caregivers (id VARCHAR PRIMARY KEY, email VARCHAR)"))
            connection.execute(text("INSERT INTO caregivers (id, email) VALUES ('existing-account', 'account@example.invalid')"))
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="reviewed identity mapping"):
                    migration.upgrade()
            columns = {column["name"] for column in inspect(connection).get_columns("caregivers")}
            assert "name" not in columns
            assert "relationship" not in columns
            assert "person_id" not in columns
            assert "timezone" not in columns
    finally:
        engine.dispose()
