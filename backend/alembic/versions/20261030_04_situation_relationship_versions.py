"""Add deterministic monotonic versions to explicit situation relationships.

Revision ID: 20261030_04_situation_relationship_versions
Revises: 20261030_03_q_answer_evidence
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, select, update

revision = "20261030_04_situation_relationship_versions"
down_revision = "20261030_03_q_answer_evidence"
branch_labels = None
depends_on = None

TABLE = "care_record_relationships"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        raise RuntimeError("care_record_relationships is required for relationship history versioning")

    columns = {item["name"] for item in inspector.get_columns(TABLE)}
    if "relationship_version" not in columns:
        op.add_column(
            TABLE,
            sa.Column("relationship_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        )

    metadata = sa.MetaData()
    relationships = sa.Table(TABLE, metadata, autoload_with=bind)
    rows = bind.execute(
        select(
            relationships.c.id,
            relationships.c.person_id,
            relationships.c.source_type,
            relationships.c.source_id,
            relationships.c.target_type,
            relationships.c.target_id,
            relationships.c.created_at,
            relationships.c.relationship_version,
        ).order_by(
            relationships.c.person_id,
            relationships.c.source_type,
            relationships.c.source_id,
            relationships.c.target_type,
            relationships.c.target_id,
            relationships.c.created_at,
            relationships.c.id,
        )
    ).mappings().all()

    versions = {}
    for row in rows:
        pair = (
            row["person_id"],
            row["source_type"],
            row["target_type"],
            *sorted((row["source_id"], row["target_id"])),
        )
        versions[pair] = versions.get(pair, 0) + 1
        # Re-number the complete historical sequence. A legacy/default value
        # of 1 on every row is not evidence of a valid version history.
        bind.execute(
            update(relationships)
            .where(relationships.c.id == row["id"])
            .values(relationship_version=versions[pair])
        )

    # Enforce the pair/version invariant at the database boundary.
    refreshed = inspect(bind)
    indexes = {item.get("name"): item for item in refreshed.get_indexes(TABLE)}
    existing_index = indexes.get("ix_care_record_relationships_pair_version")
    if existing_index and not existing_index.get("unique"):
        op.drop_index("ix_care_record_relationships_pair_version", table_name=TABLE)
        existing_index = None
    if existing_index is None:
        op.create_index(
            "ix_care_record_relationships_pair_version",
            TABLE,
            [
                "person_id", "source_type", "source_id", "target_type",
                "target_id", "relationship_version",
            ],
            unique=True,
        )

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return
    indexes = {item.get("name") for item in inspector.get_indexes(TABLE)}
    if "ix_care_record_relationships_pair_version" in indexes:
        op.drop_index("ix_care_record_relationships_pair_version", table_name=TABLE)
    columns = {item["name"] for item in inspector.get_columns(TABLE)}
    if "relationship_version" in columns:
        op.drop_column(TABLE, "relationship_version")
