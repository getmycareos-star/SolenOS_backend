import pytest

from app.core.database_config import (
    configure_database_url,
    is_railway_runtime,
    normalize_database_url,
)


def test_railway_runtime_detection():
    assert is_railway_runtime({"RAILWAY_PROJECT_ID": "project"})
    assert not is_railway_runtime({})


@pytest.mark.parametrize("url", [
    "postgres://user:pass@db.example:5432/solenos",
    "postgresql://user:pass@db.example:5432/solenos",
])
def test_postgres_urls_normalize_to_installed_driver(url):
    assert normalize_database_url(url).startswith("postgresql+psycopg2://")


def test_railway_rejects_sqlite_as_non_durable_production_storage():
    with pytest.raises(RuntimeError, match="requires DATABASE_URL|requires DATABASE_URL|Railway requires DATABASE_URL"):
        configure_database_url("sqlite:///./solenos.db", railway_runtime=True)


def test_railway_postgres_requires_tls():
    configured = configure_database_url(
        "postgresql://user:secret@db.example:5432/solenos",
        railway_runtime=True,
    )
    assert configured.startswith("postgresql+psycopg2://")
    assert "sslmode=require" in configured


def test_stronger_tls_mode_is_preserved():
    configured = configure_database_url(
        "postgresql://user:secret@db.example:5432/solenos?sslmode=verify-full",
        railway_runtime=True,
    )
    assert "sslmode=verify-full" in configured


def test_local_sqlite_remains_available_for_development():
    assert configure_database_url("sqlite:///./solenos.db", railway_runtime=False) == (
        "sqlite:///./solenos.db"
    )
