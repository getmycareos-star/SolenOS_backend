from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20261029_25_comp_turn"
down_revision = "20261029_24_baseline_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("conversation_messages"):
        raise RuntimeError("conversation_messages table is required")
    columns = {column["name"] for column in inspector.get_columns("conversation_messages")}
    if "client_turn_id" not in columns:
        op.add_column("conversation_messages", sa.Column("client_turn_id", sa.String(length=36), nullable=True))
    if "response_metadata_json" not in columns:
        op.add_column("conversation_messages", sa.Column("response_metadata_json", sa.Text(), nullable=True))
    indexes = {index["name"] for index in inspector.get_indexes("conversation_messages")}
    if "uq_conversation_messages_client_turn_id" not in indexes:
        op.create_index("uq_conversation_messages_client_turn_id", "conversation_messages", ["client_turn_id"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_conversation_messages_client_turn_id", table_name="conversation_messages")
    op.drop_column("conversation_messages", "response_metadata_json")
    op.drop_column("conversation_messages", "client_turn_id")
