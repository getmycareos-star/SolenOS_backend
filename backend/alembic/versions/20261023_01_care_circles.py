"""Persist care circles and scoped caregiver memberships.

Revision ID: 20261023_01
Revises: 20261022_01

The baseline revision registers all ORM models and creates missing tables. This
migration must therefore be idempotent when the baseline has already created
these tables and indexes.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261023_01"
down_revision = "20261022_01"
branch_labels = None
depends_on = None


def _ensure_index(bind, table_name, index_name, columns):
    inspector = inspect(bind)
    if not inspector.has_table(table_name):
        return
    existing = {item["name"] for item in inspector.get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, columns)


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("care_circles"):
        op.create_table(
            "care_circles",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
    _ensure_index(bind, "care_circles", "ix_care_circles_person_id", ["person_id"])

    inspector = inspect(bind)
    if not inspector.has_table("care_circle_memberships"):
        op.create_table(
            "care_circle_memberships",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("care_circle_id", sa.String(), sa.ForeignKey("care_circles.id"), nullable=False),
            sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=False),
            sa.Column("member_caregiver_id", sa.String(), sa.ForeignKey("caregivers.id"), nullable=False),
            sa.Column("role", sa.String(), nullable=False, server_default="family_member"),
            sa.Column("role_description", sa.Text(), nullable=True),
            sa.Column("access_level", sa.String(), nullable=False, server_default="contributor"),
            sa.Column("can_view_information", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("can_contribute_information", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("can_receive_notifications", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("can_participate_in_decisions", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("relationship_to_person", sa.String(), nullable=True),
            sa.Column("invitation_status", sa.String(), nullable=False, server_default="active"),
            sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("care_circle_id", "member_caregiver_id", name="uq_care_circle_member"),
        )
    _ensure_index(
        bind, "care_circle_memberships",
        "ix_care_circle_memberships_care_circle_id", ["care_circle_id"],
    )
    _ensure_index(
        bind, "care_circle_memberships",
        "ix_care_circle_memberships_person_id", ["person_id"],
    )
    _ensure_index(
        bind, "care_circle_memberships",
        "ix_care_circle_memberships_member_caregiver_id", ["member_caregiver_id"],
    )


def downgrade():
    # The baseline migration uses Base.metadata.create_all and can create these
    # same tables before this revision executes. Ownership is therefore
    # ambiguous; never drop caregiver-circle data during a downgrade.
    pass
