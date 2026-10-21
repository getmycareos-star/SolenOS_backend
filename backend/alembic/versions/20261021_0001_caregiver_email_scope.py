"""Bootstrap missing model tables and scope caregiver email uniqueness.

This is the initial Alembic revision for the current model set. On a fresh
PostgreSQL database, create the registered model tables before applying the
caregiver uniqueness rule; Railway runs Alembic before the ASGI application
starts, so relying on application startup's create_all() is too late.

On an existing database, create_all(checkfirst=True) only creates absent
tables; it does not alter existing tables or columns. Existing-schema drift
must still be handled by explicit, reviewed migrations and the application's
schema validation. No existing care rows are deleted or rewritten.
"""
from alembic import op
import sqlalchemy as sa

from app.core.database import Base

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

    # env.py registers the complete model set before Alembic runs revisions.
    # This creates missing tables on fresh databases and adds newly introduced
    # tables on existing databases without changing any existing table's data
    # or columns.
    Base.metadata.create_all(bind=bind, checkfirst=True)

    inspector = sa.inspect(bind)
    if not inspector.has_table("caregivers"):
        raise RuntimeError(
            "Required caregivers table was not created by the registered model metadata."
        )

    caregiver_columns = {column["name"] for column in inspector.get_columns("caregivers")}
    required_columns = {"id", "email", "person_id"}
    missing_columns = sorted(required_columns - caregiver_columns)
    if missing_columns:
        raise RuntimeError(
            "Cannot migrate caregiver email uniqueness because the existing caregivers "
            f"table is missing required columns: {', '.join(missing_columns)}. "
            "Apply a reviewed schema migration before retrying."
        )

    # Refuse the migration before any uniqueness DDL if existing records already
    # violate the target rule. Never silently delete or merge caregiver rows.
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
