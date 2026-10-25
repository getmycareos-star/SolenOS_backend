"""Allow verified professionals to publish attributed community contributions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261025_04"
down_revision = "20261025_03"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {c["name"]: c for c in inspector.get_columns("community_posts")}
    if "caregiver_id" in columns and not columns["caregiver_id"]["nullable"]:
        with op.batch_alter_table("community_posts") as batch:
            batch.alter_column("caregiver_id", existing_type=sa.String(), nullable=True)
    inspector = inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("community_posts")}
    if "professional_id" not in columns:
        op.add_column("community_posts", sa.Column("professional_id", sa.String(), sa.ForeignKey("professional_accounts.id", ondelete="CASCADE"), nullable=True))
    if "title" not in columns:
        op.add_column("community_posts", sa.Column("title", sa.String(length=200), nullable=True))
    indexes = {i["name"] for i in inspect(bind).get_indexes("community_posts")}
    if "ix_community_posts_professional_id" not in indexes:
        op.create_index("ix_community_posts_professional_id", "community_posts", ["professional_id"], unique=False)


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM community_posts WHERE professional_id IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while professional contributions exist; export or explicitly remove them first.")
    columns = {c["name"] for c in inspect(bind).get_columns("community_posts")}
    indexes = {i["name"] for i in inspect(bind).get_indexes("community_posts")}
    if "ix_community_posts_professional_id" in indexes:
        op.drop_index("ix_community_posts_professional_id", table_name="community_posts")
    if "professional_id" in columns:
        op.drop_column("community_posts", "professional_id")
    if "title" in columns:
        op.drop_column("community_posts", "title")
    with op.batch_alter_table("community_posts") as batch:
        batch.alter_column("caregiver_id", existing_type=sa.String(), nullable=False)
