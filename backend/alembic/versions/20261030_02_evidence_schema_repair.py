"""Forward-only repair for legacy claim-assessment and observation columns.

Revision ID: 20261030_02_evidence_schema_repair
Revises: 20261030_01_companion_turn_merge

Earlier revisions could already be stamped as applied when create_all() or an
older application build had created these tables without the later nullable
columns. A forward revision is required because Alembic will not replay an
already-applied historical revision.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261030_02_evidence_schema_repair"
down_revision = "20261030_01_companion_turn_merge"
branch_labels = None
depends_on = None


def _ensure_nullable_column(bind, table, name, type_):
    inspector = inspect(bind)
    if not inspector.has_table(table):
        raise RuntimeError(f"Required table {table!r} is missing; refusing partial schema repair")
    columns = {item["name"] for item in inspector.get_columns(table)}
    if name not in columns:
        op.add_column(table, sa.Column(name, type_, nullable=True))


def _ensure_foreign_key(bind, table, column, referred_table, referred_column, name):
    inspector = inspect(bind)
    matches = [
        item for item in inspector.get_foreign_keys(table)
        if column in (item.get("constrained_columns") or [])
    ]
    if any(
        item.get("referred_table") == referred_table
        and (item.get("referred_columns") or []) == [referred_column]
        for item in matches
    ):
        return
    if matches:
        raise RuntimeError(
            f"{table}.{column} already has an incompatible foreign-key definition; "
            "refusing to replace an existing constraint"
        )
    if not inspector.has_table(referred_table):
        raise RuntimeError(f"Required foreign-key target table {referred_table!r} is missing")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(name, referred_table, [column], [referred_column])
    else:
        op.create_foreign_key(name, table, referred_table, [column], [referred_column])


def upgrade() -> None:
    bind = op.get_bind()

    # These columns are nullable by design: adding them does not fabricate
    # historical corrections or supersession relationships for existing rows.
    _ensure_nullable_column(
        bind, "evidence_claim_assessments", "superseded_by_claim_id", sa.String()
    )
    _ensure_foreign_key(
        bind, "evidence_claim_assessments", "superseded_by_claim_id",
        "care_assertions", "id", "fk_evidence_claim_assessments_superseded_claim",
    )

    _ensure_nullable_column(
        bind, "evidence_observations", "supersedes_observation_id", sa.String()
    )
    _ensure_foreign_key(
        bind, "evidence_observations", "supersedes_observation_id",
        "evidence_observations", "id", "fk_evidence_observations_supersedes_observation",
    )
    _ensure_nullable_column(
        bind, "evidence_observations", "correction_rationale", sa.Text()
    )


def downgrade() -> None:
    raise RuntimeError(
        "Refusing to remove claim supersession and observation correction provenance. "
        "Use a forward-only compatibility migration."
    )
