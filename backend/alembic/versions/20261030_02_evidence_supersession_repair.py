"""Repair evidence supersession columns on partially migrated production databases.

Revision ID: 20261030_02_ev_supersession_fix
Revises: 20261030_01_companion_turn_merge

The original migrations created these nullable provenance columns, but the
production schema readiness check found them absent on an existing database.
This forward-only migration adds only missing columns/constraints and never
rewrites or deletes evidence history.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261030_02_ev_supersession_fix"
down_revision = "20261030_01_companion_turn_merge"
branch_labels = None
depends_on = None


def _has_foreign_key(bind, table: str, column: str, referred_table: str) -> bool:
    return any(
        column in (fk.get("constrained_columns") or [])
        and fk.get("referred_table") == referred_table
        for fk in inspect(bind).get_foreign_keys(table)
    )


def _ensure_nullable_fk_column(
    table: str,
    column: str,
    column_type,
    referred_table: str,
    constraint_name: str,
    referred_column: str = "id",
) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table(table):
        raise RuntimeError(f"Required evidence table is missing: {table}")

    columns = {item["name"] for item in inspector.get_columns(table)}
    if column not in columns:
        op.add_column(
            table,
            sa.Column(
                column,
                column_type,
                sa.ForeignKey(
                    f"{referred_table}.{referred_column}",
                    name=constraint_name,
                ),
                nullable=True,
            ),
        )
        return

    if _has_foreign_key(bind, table, column, referred_table):
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(
                constraint_name,
                referred_table,
                [column],
                [referred_column],
            )
    else:
        op.create_foreign_key(
            constraint_name,
            table,
            referred_table,
            [column],
            [referred_column],
        )


def upgrade() -> None:
    _ensure_nullable_fk_column(
        "evidence_claim_assessments",
        "superseded_by_claim_id",
        sa.String(),
        "care_assertions",
        "fk_evidence_claim_assessments_superseded_claim",
    )
    _ensure_nullable_fk_column(
        "evidence_observations",
        "supersedes_observation_id",
        sa.String(),
        "evidence_observations",
        "fk_evidence_observations_supersedes_observation",
    )

    bind = op.get_bind()
    inspector = inspect(bind)
    observation_columns = {
        item["name"] for item in inspector.get_columns("evidence_observations")
    }
    if "correction_rationale" not in observation_columns:
        op.add_column(
            "evidence_observations",
            sa.Column("correction_rationale", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    raise RuntimeError(
        "Forward-only repair: removing evidence supersession provenance is unsafe."
    )
