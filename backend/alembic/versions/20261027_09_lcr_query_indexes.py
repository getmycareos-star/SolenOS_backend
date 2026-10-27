"""Add person-scoped ordering indexes for bounded LCR reads.

Revision ID: 20261027_09_lcr_query_indexes
Revises: 20261027_08_privacy_delivery

Indexes follow the common query contract: filter by person_id, then fetch the
most recent bounded slice in the source's canonical event/record time order.
PostgreSQL indexes are built concurrently to avoid blocking ordinary writes.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261027_09_lcr_query_indexes"
down_revision = "20261027_08_privacy_delivery"
branch_labels = None
depends_on = None

INDEXES = (
    ("care_events", "ix_care_events_person_occurred_recorded_id", ("person_id", "occurred_at", "recorded_at", "id")),
    ("evidence", "ix_evidence_person_uploaded_id", ("person_id", "uploaded_at", "id")),
    ("care_facts", "ix_care_facts_person_recorded_id", ("person_id", "recorded_at", "id")),
    ("care_assertions", "ix_care_assertions_person_recorded_id", ("person_id", "recorded_at", "id")),
    ("appointments", "ix_appointments_person_scheduled_id", ("person_id", "scheduled_at", "id")),
    ("care_timeline_entries", "ix_care_timeline_person_occurred_id", ("person_id", "occurred_at", "id")),
    ("decision_outcomes", "ix_decision_outcomes_person_observed_id", ("person_id", "observed_at", "id")),
    ("care_uncertainties", "ix_care_uncertainties_person_created_id", ("person_id", "created_at", "id")),
    ("care_missing_information", "ix_care_missing_info_person_created_id", ("person_id", "created_at", "id")),
    ("care_conflicts", "ix_care_conflicts_person_created_id", ("person_id", "created_at", "id")),
    ("caregiver_observations", "ix_caregiver_observations_person_observed_id", ("person_id", "observed_at", "id")),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    for table_name, index_name, columns in INDEXES:
        if table_name not in tables:
            continue
        available_columns = {column["name"] for column in inspector.get_columns(table_name)}
        if not set(columns).issubset(available_columns):
            # Older or partially reconciled schemas must not be indexed against
            # columns they do not have. The source reader separately reports gaps.
            continue
        existing = {index["name"] for index in inspector.get_indexes(table_name)}
        if index_name in existing:
            continue

        if bind.dialect.name == "postgresql":
            # CREATE INDEX CONCURRENTLY cannot run inside the migration transaction.
            # Autocommit also lets a retry safely skip indexes already completed.
            with op.get_context().autocommit_block():
                op.create_index(index_name, table_name, list(columns), postgresql_concurrently=True)
        else:
            op.create_index(index_name, table_name, list(columns))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    for table_name, index_name, _columns in reversed(INDEXES):
        if table_name not in tables:
            continue
        existing = {index["name"] for index in inspector.get_indexes(table_name)}
        if index_name not in existing:
            continue
        if bind.dialect.name == "postgresql":
            with op.get_context().autocommit_block():
                op.drop_index(index_name, table_name=table_name, postgresql_concurrently=True)
        else:
            op.drop_index(index_name, table_name=table_name)
