"""Verify the combined Alembic schema on the configured staging PostgreSQL database."""
from __future__ import annotations

import os
from sqlalchemy import create_engine, inspect, text


EXPECTED_HEAD = "t4_document_assets_20261023"
REQUIRED_TABLES = {
    "auth_accounts",
    "auth_sessions",
    "auth_account_person_access",
    "auth_caregiver_account_links",
    "auth_caregiver_invitations",
    "auth_caregiver_profile_account_links",
    "auth_caregiver_profile_invitations",
    "anonymous_sessions",
    "daily_captures",
    "evidence_files",
    "persons",
    "caregivers",
    "care_events",
    "evidence",
    "actions",
    "interaction_sessions",
    "care_circles",
    "care_circle_memberships",
    "care_delta_runs",
    "care_assertions",
    "care_action_outcomes",
    "care_reminders",
    "reminder_deliveries",
    "reminder_delivery_attempts",
    "medication_authority_verifications",
    "care_state_revisions",
    "care_handoffs",
    "care_handoff_events",
    "capture_analyses",
    "document_assets",
    "community_posts",
    "community_reactions",
    "conversation_sessions",
    "conversation_messages",
}


def main() -> None:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        raise SystemExit("Refusing schema verification: DATABASE_URL is not PostgreSQL.")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        if engine.dialect.name != "postgresql":
            raise SystemExit("Refusing schema verification against a non-PostgreSQL database.")
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            raise SystemExit("Staging schema is missing required tables: " + ", ".join(missing))
        with engine.connect() as connection:
            revisions = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
        if revisions != {EXPECTED_HEAD}:
            raise SystemExit(f"Staging Alembic revision is not the single expected head ({EXPECTED_HEAD}).")
        print(f"Staging PostgreSQL schema verified: {len(REQUIRED_TABLES)} required tables present; one Alembic head.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
