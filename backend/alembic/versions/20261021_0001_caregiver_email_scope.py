"""Scope caregiver email uniqueness to a care record.

Existing PostgreSQL installations may have a legacy UNIQUE(email) constraint.
This explicit migration replaces that constraint with uniqueness on
(person_id, email); it is not run implicitly during application startup.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_0001"
down_revision = None
branch_labels = None
depends_on = None


def _has_unique(bind, table, columns):
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return False
    wanted = list(columns)
    for constraint in inspector.get_unique_constraints(table):
        if constraint.get("column_names") == wanted:
            return True
    for index in inspector.get_indexes(table):
        if index.get("unique") and index.get("column_names") == wanted:
            return True
    return False


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    if not inspector.has_table("caregivers"):
        # A brand-new database is bootstrapped by the registered model metadata.
        return

    # Refuse the migration before any DDL if existing records already violate
    # the target uniqueness rule. Never silently delete or merge caregiver rows.
    duplicate = bind.execute(sa.text(
        "SELECT person_id, email FROM caregivers "
        "GROUP BY person_id, email HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate:
        raise RuntimeError(
            "Cannot scope caregiver email uniqueness: duplicate email values exist "
            "within a care record. Resolve duplicates explicitly before migration."
        )

    constraints = inspector.get_unique_constraints("caregivers")
    constraint_names = {item.get("name") for item in constraints}
    for constraint in constraints:
        if constraint.get("column_names") == ["email"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "caregivers", type_="unique")

    for index in inspector.get_indexes("caregivers"):
        if (
            index.get("unique")
            and index.get("column_names") == ["email"]
            and index.get("name") not in constraint_names
        ):
            op.drop_index(index["name"], table_name="caregivers")

    if not _has_unique(bind, "caregivers", ["person_id", "email"]):
        op.create_index(
            "uq_caregiver_person_email",
            "caregivers",
            ["person_id", "email"],
            unique=True,
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    if not inspector.has_table("caregivers"):
        return

    for constraint in inspector.get_unique_constraints("caregivers"):
        if constraint.get("column_names") == ["person_id", "email"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "caregivers", type_="unique")
    for index in inspector.get_indexes("caregivers"):
        if index.get("unique") and index.get("column_names") == ["person_id", "email"]:
            op.drop_index(index["name"], table_name="caregivers")

    duplicate = bind.execute(sa.text(
        "SELECT email FROM caregivers GROUP BY email HAVING COUNT(*) > 1 LIMIT 1"
    )).first()
    if duplicate:
        raise RuntimeError(
            "Cannot downgrade caregiver email scope: duplicate emails exist across care records."
        )
    if not _has_unique(bind, "caregivers", ["email"]):
        op.create_index("uq_caregivers_email", "caregivers", ["email"], unique=True)
