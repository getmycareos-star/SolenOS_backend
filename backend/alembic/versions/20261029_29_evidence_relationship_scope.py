"""Scope source relationship reviews to a claim, observation, or event.

Revision ID: 20261029_29_ev_rel_scope
Revises: 20261029_28_conflict_remainder
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_29_ev_rel_scope"
down_revision = "20261029_28_conflict_remainder"
branch_labels = None
depends_on = None

TABLE = "evidence_source_relationships"
OLD_CONSTRAINT = "uq_evidence_source_relationship"
NEW_CONSTRAINT = "uq_evidence_source_relationship"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        raise RuntimeError("Required evidence_source_relationships table is missing")

    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    unique_constraints = {item.get("name") for item in inspector.get_unique_constraints(TABLE)}
    needs_scope_type = "scope_type" not in columns
    needs_scope_id = "scope_id" not in columns
    needs_constraint_rebuild = OLD_CONSTRAINT in unique_constraints

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(TABLE) as batch:
            if needs_scope_type:
                batch.add_column(sa.Column(
                    "scope_type", sa.String(length=24), nullable=False,
                    server_default=sa.text("'evidence_pair'"),
                ))
            if needs_scope_id:
                batch.add_column(sa.Column(
                    "scope_id", sa.String(length=128), nullable=False,
                    server_default=sa.text("''"),
                ))
            if needs_constraint_rebuild:
                batch.drop_constraint(OLD_CONSTRAINT, type_="unique")
            if needs_constraint_rebuild or NEW_CONSTRAINT not in unique_constraints:
                batch.create_unique_constraint(NEW_CONSTRAINT, [
                    "person_id", "source_evidence_id", "related_evidence_id",
                    "relationship_type", "scope_type", "scope_id",
                ])
        return

    if needs_scope_type:
        op.add_column(TABLE, sa.Column(
            "scope_type", sa.String(length=24), nullable=False,
            server_default=sa.text("'evidence_pair'"),
        ))
    if needs_scope_id:
        op.add_column(TABLE, sa.Column(
            "scope_id", sa.String(length=128), nullable=False,
            server_default=sa.text("''"),
        ))
    if needs_constraint_rebuild:
        op.drop_constraint(OLD_CONSTRAINT, TABLE, type_="unique")
    refreshed = {item.get("name") for item in inspect(bind).get_unique_constraints(TABLE)}
    if NEW_CONSTRAINT not in refreshed:
        op.create_unique_constraint(NEW_CONSTRAINT, TABLE, [
            "person_id", "source_evidence_id", "related_evidence_id",
            "relationship_type", "scope_type", "scope_id",
        ])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return
    columns = {column["name"] for column in inspector.get_columns(TABLE)}
    unique_constraints = {item.get("name") for item in inspector.get_unique_constraints(TABLE)}
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(TABLE) as batch:
            if NEW_CONSTRAINT in unique_constraints:
                batch.drop_constraint(NEW_CONSTRAINT, type_="unique")
            if OLD_CONSTRAINT not in unique_constraints:
                batch.create_unique_constraint(OLD_CONSTRAINT, [
                    "person_id", "source_evidence_id", "related_evidence_id", "relationship_type",
                ])
            if "scope_id" in columns:
                batch.drop_column("scope_id")
            if "scope_type" in columns:
                batch.drop_column("scope_type")
        return
    if NEW_CONSTRAINT in unique_constraints:
        op.drop_constraint(NEW_CONSTRAINT, TABLE, type_="unique")
    refreshed = {item.get("name") for item in inspect(bind).get_unique_constraints(TABLE)}
    if OLD_CONSTRAINT not in refreshed:
        op.create_unique_constraint(OLD_CONSTRAINT, TABLE, [
            "person_id", "source_evidence_id", "related_evidence_id", "relationship_type",
        ])
    if "scope_id" in columns:
        op.drop_column(TABLE, "scope_id")
    if "scope_type" in columns:
        op.drop_column(TABLE, "scope_type")
