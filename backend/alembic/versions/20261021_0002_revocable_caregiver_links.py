"""Permit re-linking after revocation while enforcing one active link per identity.

Revision ID: 20261021_0002
Revises: 20261021_0001
"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_0002"
down_revision = "20261021_0001"
branch_labels = None
depends_on = None

ACTIVE = sa.text("is_active = true AND revoked_at IS NULL")


def _columns_match(item, columns):
    return item.get("column_names") is not None and set(item["column_names"]) == set(columns)


def _replace_link_uniqueness(bind, table, identity_column, old_pair_name, new_identity_index, new_pair_index):
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        raise RuntimeError(f"Required table {table} is missing; refusing to migrate caregiver link uniqueness.")

    # Drop legacy global unique constraints, including the pair constraint that
    # would otherwise prevent the same account from being re-linked after revocation.
    for constraint in inspector.get_unique_constraints(table):
        columns = constraint.get("column_names") or []
        if _columns_match(constraint, [identity_column]) or _columns_match(constraint, ["account_id", identity_column]):
            name = constraint.get("name")
            if name:
                op.drop_constraint(name, table, type_="unique")

    # SQLAlchemy may represent unique=True,index=True as a unique index instead
    # of a table constraint. Preserve the target partial-index names.
    inspector = sa.inspect(bind)
    target_names = {new_identity_index, new_pair_index}
    for index in inspector.get_indexes(table):
        columns = index.get("column_names") or []
        if index.get("unique") and (
            _columns_match(index, [identity_column]) or _columns_match(index, ["account_id", identity_column])
        ) and index.get("name") not in target_names:
            op.drop_index(index["name"], table_name=table)

    inspector = sa.inspect(bind)
    existing = {item.get("name") for item in inspector.get_indexes(table)}
    if new_identity_index not in existing:
        op.create_index(
            new_identity_index, table, [identity_column], unique=True,
            postgresql_where=ACTIVE,
        )
    if new_pair_index not in existing:
        op.create_index(
            new_pair_index, table, ["account_id", identity_column], unique=True,
            postgresql_where=ACTIVE,
        )


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    _replace_link_uniqueness(
        bind,
        "auth_caregiver_account_links",
        "caregiver_id",
        "uq_auth_caregiver_account_link",
        "uq_active_caregiver_link_caregiver",
        "uq_active_caregiver_link_account_caregiver",
    )
    _replace_link_uniqueness(
        bind,
        "auth_caregiver_profile_account_links",
        "caregiver_profile_id",
        "uq_auth_caregiver_profile_account_link",
        "uq_active_caregiver_profile_link_profile",
        "uq_active_caregiver_profile_link_account_profile",
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Do not restore the old global uniqueness rules: doing so can fail when
    # legitimate re-link history exists and would make revoked links permanent.
    for table, names in (
        ("auth_caregiver_account_links", (
            "uq_active_caregiver_link_caregiver",
            "uq_active_caregiver_link_account_caregiver",
        )),
        ("auth_caregiver_profile_account_links", (
            "uq_active_caregiver_profile_link_profile",
            "uq_active_caregiver_profile_link_account_profile",
        )),
    ):
        inspector = sa.inspect(bind)
        if inspector.has_table(table):
            existing = {item.get("name") for item in inspector.get_indexes(table)}
            for name in names:
                if name in existing:
                    op.drop_index(name, table_name=table)
