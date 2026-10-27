"""Bind caregiver profiles to server-issued caregiver identities.

Revision ID: 20261028_01_profile_link
Revises: 20261027_11_delta_runs_repair

Anonymous sessions do not have email addresses. A separate nullable link lets the
API resolve a profile without trusting client-provided caregiver IDs.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "20261028_01_profile_link"
down_revision = "20261027_11_delta_runs_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if "caregiver_profiles" not in tables or "caregivers" not in tables:
        raise RuntimeError("Cannot link caregiver profiles: caregiver_profiles/caregivers tables are required")

    columns = {column["name"]: column for column in inspector.get_columns("caregiver_profiles")}
    if "email" in columns and columns["email"].get("nullable") is False:
        op.alter_column(
            "caregiver_profiles", "email",
            existing_type=columns["email"]["type"],
            existing_nullable=False,
            nullable=True,
        )

    if "session_caregiver_id" not in columns:
        op.add_column(
            "caregiver_profiles",
            sa.Column("session_caregiver_id", sa.String(), nullable=True),
        )

    inspector = inspect(bind)
    foreign_keys = inspector.get_foreign_keys("caregiver_profiles")
    if not any("session_caregiver_id" in fk.get("constrained_columns", []) for fk in foreign_keys):
        op.create_foreign_key(
            "fk_caregiver_profiles_session_caregiver",
            "caregiver_profiles", "caregivers",
            ["session_caregiver_id"], ["id"],
            ondelete="SET NULL",
        )

    inspector = inspect(bind)
    unique_constraints = inspector.get_unique_constraints("caregiver_profiles")
    indexes = inspector.get_indexes("caregiver_profiles")
    has_unique = any(
        constraint.get("column_names") == ["session_caregiver_id"]
        for constraint in unique_constraints
    ) or any(
        index.get("unique") and index.get("column_names") == ["session_caregiver_id"]
        for index in indexes
    )
    if not has_unique:
        op.create_unique_constraint(
            "uq_caregiver_profiles_session_caregiver",
            "caregiver_profiles",
            ["session_caregiver_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregiver_profiles"):
        return
    columns = {column["name"]: column for column in inspector.get_columns("caregiver_profiles")}
    if "email" in columns and columns["email"].get("nullable") is True:
        has_anonymous_profiles = bind.execute(text(
            "SELECT 1 FROM caregiver_profiles WHERE email IS NULL LIMIT 1"
        )).first()
        if has_anonymous_profiles:
            raise RuntimeError(
                "Cannot downgrade caregiver profile identity link while anonymous profiles without email exist"
            )
    if "session_caregiver_id" not in columns:
        return

    inspector = inspect(bind)
    for fk in inspector.get_foreign_keys("caregiver_profiles"):
        if "session_caregiver_id" in fk.get("constrained_columns", []) and fk.get("name"):
            op.drop_constraint(fk["name"], "caregiver_profiles", type_="foreignkey")
    inspector = inspect(bind)
    for constraint in inspector.get_unique_constraints("caregiver_profiles"):
        if constraint.get("column_names") == ["session_caregiver_id"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "caregiver_profiles", type_="unique")
    for index in inspect(bind).get_indexes("caregiver_profiles"):
        if index.get("unique") and index.get("column_names") == ["session_caregiver_id"] and index.get("name"):
            op.drop_index(index["name"], table_name="caregiver_profiles")
    op.drop_column("caregiver_profiles", "session_caregiver_id")
    if "email" in columns and columns["email"].get("nullable") is True:
        op.alter_column(
            "caregiver_profiles", "email",
            existing_type=columns["email"]["type"],
            existing_nullable=True,
            nullable=False,
        )
