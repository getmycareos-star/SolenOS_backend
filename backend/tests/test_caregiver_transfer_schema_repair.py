from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def _load_repair_migration():
    backend_dir = Path(__file__).resolve().parents[1]
    path = backend_dir / "alembic" / "versions" / "20261115_02_repair_caregiver_transfers.py"
    spec = spec_from_file_location("repair_caregiver_transfers_migration", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_caregiver_transfer_repair_recreates_missing_table_and_is_idempotent():
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            metadata = sa.MetaData()
            sa.Table("persons", metadata, sa.Column("id", sa.String(), primary_key=True))
            sa.Table("caregivers", metadata, sa.Column("id", sa.String(), primary_key=True))
            sa.Table("caregiver_profiles", metadata, sa.Column("id", sa.String(), primary_key=True))
            sa.Table("caregiver_responsibilities", metadata, sa.Column("id", sa.String(), primary_key=True))
            metadata.create_all(connection)

            migration = _load_repair_migration()
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()

            inspector = sa.inspect(connection)
            assert inspector.has_table("caregiver_responsibility_transfers")
            columns = {column["name"] for column in inspector.get_columns("caregiver_responsibility_transfers")}
            assert columns == {
                "id",
                "person_id",
                "source_responsibility_id",
                "from_caregiver_profile_id",
                "to_caregiver_profile_id",
                "created_by_caregiver_id",
                "related_record_type",
                "related_record_id",
                "reason",
                "status",
                "created_at",
                "accepted_at",
                "accepted_by_caregiver_id",
            }
            indexes = {item["name"] for item in inspector.get_indexes("caregiver_responsibility_transfers")}
            assert {
                "ix_caregiver_responsibility_transfers_person_id",
                "ix_caregiver_responsibility_transfers_source_responsibility_id",
                "ix_caregiver_responsibility_transfers_status",
            } <= indexes

            # Re-running the repair must preserve the existing table and indexes.
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert sa.inspect(connection).has_table("caregiver_responsibility_transfers")
    finally:
        engine.dispose()
