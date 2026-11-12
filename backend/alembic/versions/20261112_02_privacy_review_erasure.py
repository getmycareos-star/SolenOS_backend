"""Allow document review event deletion only during approved privacy erasure.

Revision ID: 20261112_02_privacy_erasure
Revises: 20261112_01_care_execution_status
"""
from alembic import op

revision = "20261112_02_privacy_erasure"
down_revision = "20261112_01_care_exec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_document_review_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE'
                   AND current_setting('solenos.privacy_erasure', true) = 'on' THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'document review events are append-only outside approved privacy erasure';
            END;
            $$ LANGUAGE plpgsql
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_document_review_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'document review events are append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
