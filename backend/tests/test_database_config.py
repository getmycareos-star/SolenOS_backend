import pytest
from sqlalchemy import create_engine, text

from app.core.database import configure_database_url, normalize_database_url, validate_database_schema
from app.models.care import Person


def test_normalize_legacy_postgres_scheme():
    assert normalize_database_url("postgres://user:pass@host:5432/db") == (
        "postgresql+psycopg://user:pass@host:5432/db"
    )


def test_normalize_postgresql_scheme():
    assert normalize_database_url("postgresql://user:pass@host:5432/db") == (
        "postgresql+psycopg://user:pass@host:5432/db"
    )


def test_preserve_sqlite_scheme_outside_railway():
    assert configure_database_url("sqlite:///./solenos.db", railway_runtime=False) == "sqlite:///./solenos.db"


def test_reject_ephemeral_sqlite_in_railway():
    with pytest.raises(RuntimeError, match="must use PostgreSQL"):
        configure_database_url("sqlite:///./solenos.db", railway_runtime=True)


@pytest.mark.parametrize("url", ["", "   ", "mysql://user:pass@host/db"])
def test_reject_missing_or_unsupported_production_database_url(url):
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        configure_database_url(url, railway_runtime=True)


def test_allow_postgres_in_railway():
    assert configure_database_url(
        "postgresql://user:pass@host:5432/db",
        railway_runtime=True,
    ) == "postgresql+psycopg://user:pass@host:5432/db?sslmode=require"


def test_reject_placeholder_session_secret_in_railway():
    from app.core.config import validate_deployment_secret

    with pytest.raises(RuntimeError, match="SECRET_KEY must be configured in Railway"):
        validate_deployment_secret("change-me-in-production", railway_runtime=True)


@pytest.mark.parametrize("secret", ["", "short", "   "])
def test_reject_empty_or_short_session_secret_in_railway(secret):
    from app.core.config import validate_deployment_secret

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        validate_deployment_secret(secret, railway_runtime=True)


def test_allow_configured_session_secret_in_railway():
    from app.core.config import validate_deployment_secret

    validate_deployment_secret("a-real-secret-from-the-environment", railway_runtime=True)


def test_development_settings_do_not_default_to_a_committed_placeholder():
    from app.core.config import Settings, validate_deployment_secret

    assert Settings().secret_key == ""
    validate_deployment_secret("", railway_runtime=False)

def test_schema_inspection_failure_fails_closed():
    from app.core.database import ensure_database_schema

    with pytest.raises(RuntimeError, match="Unable to inspect the configured database schema"):
        ensure_database_schema(bind=object())




def test_schema_validation_rejects_missing_columns_on_existing_tables():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE persons (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL)"))
        with pytest.raises(RuntimeError, match=r"persons:.*date_of_birth"):
            validate_database_schema(engine)
    finally:
        engine.dispose()


def test_schema_validation_accepts_a_model_complete_existing_schema():
    from app.core.database import Base, _register_all_models

    engine = create_engine("sqlite://")
    try:
        _register_all_models()
        Base.metadata.create_all(bind=engine)
        validate_database_schema(engine)
    finally:
        engine.dispose()


def test_schema_validation_rejects_missing_registered_table():
    from app.core.database import Base, _register_all_models

    engine = create_engine("sqlite://")
    try:
        _register_all_models()
        Person.__table__.create(engine)
        with pytest.raises(RuntimeError, match=r"\(table missing\)"):
            validate_database_schema(engine)
    finally:
        engine.dispose()

def test_revoked_caregiver_link_history_does_not_block_relinking():
    from datetime import datetime, timezone
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session
    from app.core.database import Base
    from app.models.auth_identity import AuthAccount, CaregiverAccountLink
    from app.models.care import Person, Caregiver

    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(bind=engine, tables=[
            Person.__table__, Caregiver.__table__, AuthAccount.__table__, CaregiverAccountLink.__table__
        ])
        with Session(engine) as db:
            person = Person(name="Care recipient")
            account_one = AuthAccount(email="one@example.test", password_hash="hash")
            account_two = AuthAccount(email="two@example.test", password_hash="hash")
            db.add_all([person, account_one, account_two])
            db.flush()
            caregiver = Caregiver(
                name="Family caregiver", email="caregiver@example.test",
                relationship="family", person_id=person.id, timezone="UTC"
            )
            db.add(caregiver)
            db.flush()
            first_link = CaregiverAccountLink(
                account_id=account_one.id, caregiver_id=caregiver.id, person_id=person.id,
                linked_by_account_id=account_one.id, is_active=True
            )
            db.add(first_link)
            db.commit()

            # The partial unique index must reject a second active link.
            db.add(CaregiverAccountLink(
                account_id=account_two.id, caregiver_id=caregiver.id, person_id=person.id,
                linked_by_account_id=account_one.id, is_active=True
            ))
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()

            # But once the prior link is revoked, a new account can be linked
            # while preserving the historical row.
            first_link = db.query(CaregiverAccountLink).filter_by(account_id=account_one.id).one()
            first_link.is_active = False
            first_link.revoked_at = datetime.now(timezone.utc)
            db.commit()
            replacement = CaregiverAccountLink(
                account_id=account_two.id, caregiver_id=caregiver.id, person_id=person.id,
                linked_by_account_id=account_one.id, is_active=True
            )
            db.add(replacement)
            db.commit()
            assert db.query(CaregiverAccountLink).count() == 2
            assert db.query(CaregiverAccountLink).filter_by(is_active=True, revoked_at=None).count() == 1
    finally:
        engine.dispose()




def test_railway_postgres_url_requires_tls_and_overrides_insecure_mode():
    configured = configure_database_url(
        "postgresql://user:pass@host:5432/db?sslmode=disable",
        railway_runtime=True,
    )
    assert configured == "postgresql+psycopg://user:pass@host:5432/db?sslmode=require"


def test_local_postgres_url_does_not_force_tls():
    configured = configure_database_url(
        "postgresql://user:pass@host:5432/db",
        railway_runtime=False,
    )
    assert configured == "postgresql+psycopg://user:pass@host:5432/db"
