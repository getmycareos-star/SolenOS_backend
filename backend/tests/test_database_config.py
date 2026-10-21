import pytest

from app.core.database import configure_database_url, normalize_database_url


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
    with pytest.raises(RuntimeError, match="must use PostgreSQL in Railway"):
        configure_database_url("sqlite:///./solenos.db", railway_runtime=True)


def test_allow_postgres_in_railway():
    assert configure_database_url(
        "postgresql://user:pass@host:5432/db",
        railway_runtime=True,
    ) == "postgresql+psycopg://user:pass@host:5432/db"


def test_reject_placeholder_session_secret_in_railway():
    from app.core.config import validate_deployment_secret

    with pytest.raises(RuntimeError, match="SECRET_KEY must be configured in Railway"):
        validate_deployment_secret("change-me-in-production", railway_runtime=True)


def test_allow_configured_session_secret_in_railway():
    from app.core.config import validate_deployment_secret

    validate_deployment_secret("a-real-secret-from-the-environment", railway_runtime=True)


def test_allow_development_placeholder_outside_railway():
    from app.core.config import validate_deployment_secret

    validate_deployment_secret("change-me-in-production", railway_runtime=False)
