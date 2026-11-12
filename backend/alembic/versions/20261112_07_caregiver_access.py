"""Add server-enforced caregiver membership grants and allow multiple caregiver accounts.

Revision ID: 20261112_07_caregiver_access
Revises: 20261112_06_notification_lifecycle
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261112_07_caregiver_access"
down_revision = "20261112_06_notification_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("caregiver_access_grants"):
        op.create_table(
            "caregiver_access_grants",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("caregiver_profile_id", sa.String(), sa.ForeignKey("caregiver_profiles.id", ondelete="SET NULL"), nullable=True),
            sa.Column("role", sa.String(32), nullable=False, server_default="family"),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("can_manage_members", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("granted_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("person_id", "caregiver_id", name="uq_caregiver_access_person_caregiver"),
        )
    existing_indexes = {item["name"] for item in inspect(bind).get_indexes("caregiver_access_grants")}
    for name, columns in (
        ("ix_caregiver_access_grants_person_id", ["person_id"]),
        ("ix_caregiver_access_grants_caregiver_id", ["caregiver_id"]),
        ("ix_caregiver_access_grants_caregiver_profile_id", ["caregiver_profile_id"]),
        ("ix_caregiver_access_grants_status", ["status"]),
    ):
        if name not in existing_indexes:
            op.create_index(name, "caregiver_access_grants", columns)

    # Existing server-issued sessions are the only historical proof that a
    # caregiver identity was granted record access. Do not grant access merely
    # because a profile or email happens to exist.
    op.execute(sa.text("""
        INSERT INTO caregiver_access_grants
            (id, person_id, caregiver_id, caregiver_profile_id, role, status, can_manage_members, granted_at)
        SELECT
            lower(hex(randomblob(16))),
            s.person_id,
            s.caregiver_id,
            p.id,
            CASE WHEN ranked.position = 1 THEN 'primary' ELSE 'family' END,
            'active',
            CASE WHEN ranked.position = 1 THEN TRUE ELSE FALSE END,
            COALESCE(s.created_at, CURRENT_TIMESTAMP)
        FROM (
            SELECT person_id, caregiver_id, MIN(created_at) AS first_created_at,
                   ROW_NUMBER() OVER (PARTITION BY person_id ORDER BY MIN(created_at), caregiver_id) AS position
            FROM anonymous_sessions
            GROUP BY person_id, caregiver_id
        ) ranked
        JOIN anonymous_sessions s
          ON s.person_id = ranked.person_id
         AND s.caregiver_id = ranked.caregiver_id
         AND s.created_at = ranked.first_created_at
        LEFT JOIN caregiver_profiles p
          ON p.person_id = s.person_id AND p.session_caregiver_id = s.caregiver_id
        WHERE NOT EXISTS (
            SELECT 1 FROM caregiver_access_grants g
            WHERE g.person_id = s.person_id AND g.caregiver_id = s.caregiver_id
        )
    """)) if bind.dialect.name == "sqlite" else op.execute(sa.text("""
        INSERT INTO caregiver_access_grants
            (id, person_id, caregiver_id, caregiver_profile_id, role, status, can_manage_members, granted_at)
        SELECT
            gen_random_uuid()::text,
            ranked.person_id,
            ranked.caregiver_id,
            p.id,
            CASE WHEN ranked.position = 1 THEN 'primary' ELSE 'family' END,
            'active',
            CASE WHEN ranked.position = 1 THEN TRUE ELSE FALSE END,
            ranked.first_created_at
        FROM (
            SELECT person_id, caregiver_id, MIN(created_at) AS first_created_at,
                   ROW_NUMBER() OVER (PARTITION BY person_id ORDER BY MIN(created_at), caregiver_id) AS position
            FROM anonymous_sessions
            GROUP BY person_id, caregiver_id
        ) ranked
        LEFT JOIN caregiver_profiles p
          ON p.person_id = ranked.person_id AND p.session_caregiver_id = ranked.caregiver_id
        WHERE NOT EXISTS (
            SELECT 1 FROM caregiver_access_grants g
            WHERE g.person_id = ranked.person_id AND g.caregiver_id = ranked.caregiver_id
        )
    """))

    if not inspect(bind).has_table("caregiver_invitations"):
        op.create_table(
            "caregiver_invitations",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("email_normalized", sa.String(320), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("invited_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("role", sa.String(32), nullable=False, server_default="family"),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("accepted_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("token_hash", name="uq_caregiver_invitations_token_hash"),
        )
        op.create_index("ix_caregiver_invitations_person_id", "caregiver_invitations", ["person_id"])
        op.create_index("ix_caregiver_invitations_email_normalized", "caregiver_invitations", ["email_normalized"])
        op.create_index("ix_caregiver_invitations_status", "caregiver_invitations", ["status"])

    # Multi-caregiver accounts are required for independently authenticated
    # family members. Email remains globally unique.
    constraints = inspector.get_unique_constraints("caregiver_accounts") if inspector.has_table("caregiver_accounts") else []
    for constraint in constraints:
        if constraint.get("column_names") == ["person_id"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "caregiver_accounts", type_="unique")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("caregiver_accounts"):
        constraints = inspector.get_unique_constraints("caregiver_accounts")
        if not any(item.get("column_names") == ["person_id"] for item in constraints):
            op.create_unique_constraint("uq_caregiver_accounts_person_id", "caregiver_accounts", ["person_id"])
    if inspector.has_table("caregiver_access_grants"):
        for item in inspect(bind).get_indexes("caregiver_access_grants"):
            if item.get("name", "").startswith("ix_caregiver_access_grants_"):
                op.drop_index(item["name"], table_name="caregiver_access_grants")
        op.drop_table("caregiver_access_grants")
