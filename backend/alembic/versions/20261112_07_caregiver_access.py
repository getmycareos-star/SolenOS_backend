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
            sa.Column("access_scope", sa.String(32), nullable=False, server_default="full_record"),
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
    # because a profile or email happens to exist. The baseline migration may
    # already have created the current ORM shape (including access_scope), while
    # older deployed schemas may not have that column yet.
    grant_columns = {item["name"] for item in inspect(bind).get_columns("caregiver_access_grants")}
    if "access_scope" not in grant_columns:
        op.add_column(
            "caregiver_access_grants",
            sa.Column("access_scope", sa.String(32), nullable=False, server_default="full_record"),
        )

    if bind.dialect.name == "sqlite":
        grant_id = "lower(hex(randomblob(16)))"
    else:
        grant_id = "gen_random_uuid()::text"

    # Only an explicitly designated primary family caregiver receives
    # administration rights. Session age is not evidence of authority.
    op.execute(sa.text(f"""
        WITH explicit_primary_profiles AS (
            SELECT person_id, id AS caregiver_profile_id
            FROM caregiver_profiles
            WHERE is_primary_designated = TRUE
            UNION
            SELECT person_id, caregiver_id AS caregiver_profile_id
            FROM caregiver_roles
            WHERE is_current = TRUE
              AND CAST(role_type AS VARCHAR) IN ('PRIMARY_FAMILY_CAREGIVER', 'primary_family_caregiver')
            UNION
            SELECT p.person_id, p.id AS caregiver_profile_id
            FROM caregiver_profiles p
            JOIN caregiver_accounts a
              ON a.person_id = p.person_id
             AND a.caregiver_id = p.session_caregiver_id
        ),
        primary_counts AS (
            SELECT person_id, COUNT(*) AS primary_count
            FROM explicit_primary_profiles
            GROUP BY person_id
        ),
        session_identities AS (
            SELECT person_id, caregiver_id, MIN(created_at) AS first_created_at
            FROM anonymous_sessions
            GROUP BY person_id, caregiver_id
        )
        INSERT INTO caregiver_access_grants
            (id, person_id, caregiver_id, caregiver_profile_id, role, access_scope, status, can_manage_members, granted_at)
        SELECT
            {grant_id},
            s.person_id,
            s.caregiver_id,
            p.id,
            CASE WHEN pc.primary_count = 1 AND ep.caregiver_profile_id = p.id THEN 'primary' ELSE 'family' END,
            'full_record',
            'active',
            CASE WHEN pc.primary_count = 1 AND ep.caregiver_profile_id = p.id THEN TRUE ELSE FALSE END,
            s.first_created_at
        FROM session_identities s
        LEFT JOIN caregiver_profiles p
          ON p.person_id = s.person_id AND p.session_caregiver_id = s.caregiver_id
        LEFT JOIN explicit_primary_profiles ep
          ON ep.person_id = s.person_id AND ep.caregiver_profile_id = p.id
        LEFT JOIN primary_counts pc ON pc.person_id = s.person_id
        WHERE NOT EXISTS (
            SELECT 1 FROM caregiver_access_grants g
            WHERE g.person_id = s.person_id AND g.caregiver_id = s.caregiver_id
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

    if not inspect(bind).has_table("caregiver_responsibility_transfers"):
        op.create_table(
            "caregiver_responsibility_transfers",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_responsibility_id", sa.String(), sa.ForeignKey("caregiver_responsibilities.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("from_caregiver_profile_id", sa.String(), sa.ForeignKey("caregiver_profiles.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("to_caregiver_profile_id", sa.String(), sa.ForeignKey("caregiver_profiles.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("created_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("related_record_type", sa.String(40), nullable=True),
            sa.Column("related_record_id", sa.String(160), nullable=True),
            sa.Column("reason", sa.String(1000), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("accepted_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id", ondelete="SET NULL"), nullable=True),
        )
        op.create_index("ix_caregiver_responsibility_transfers_person_id", "caregiver_responsibility_transfers", ["person_id"])
        op.create_index("ix_caregiver_responsibility_transfers_source_responsibility_id", "caregiver_responsibility_transfers", ["source_responsibility_id"])
        op.create_index("ix_caregiver_responsibility_transfers_status", "caregiver_responsibility_transfers", ["status"])

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
    if inspector.has_table("caregiver_responsibility_transfers"):
        for item in inspect(bind).get_indexes("caregiver_responsibility_transfers"):
            op.drop_index(item["name"], table_name="caregiver_responsibility_transfers")
        op.drop_table("caregiver_responsibility_transfers")
    if inspector.has_table("caregiver_invitations"):
        # The token hash unique constraint owns its backing index. Drop only
        # standalone lookup indexes; DROP TABLE removes the constraint safely.
        for item in inspect(bind).get_indexes("caregiver_invitations"):
            if item.get("name", "").startswith("ix_caregiver_invitations_"):
                op.drop_index(item["name"], table_name="caregiver_invitations")
        op.drop_table("caregiver_invitations")
    if inspector.has_table("caregiver_access_grants"):
        for item in inspect(bind).get_indexes("caregiver_access_grants"):
            if item.get("name", "").startswith("ix_caregiver_access_grants_"):
                op.drop_index(item["name"], table_name="caregiver_access_grants")
        op.drop_table("caregiver_access_grants")
