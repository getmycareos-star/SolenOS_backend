import pytest

from app.core.database_config import (
    configure_database_url,
    is_railway_runtime,
    normalize_database_url,
)


def test_railway_runtime_detected_from_railway_project_id():
    assert is_railway_runtime({"RAILWAY_PROJECT_ID": "project-123"})
    assert not is_railway_runtime({})


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("postgres://user:pass@db.example:5432/solenos", "postgresql+psycopg2://"),
        ("postgresql://user:pass@db.example:5432/solenos", "postgresql+psycopg2://"),
    ],
)
def test_hosted_postgres_urls_are_normalized(source, expected):
    assert normalize_database_url(source).startswith(expected)


def test_railway_rejects_sqlite_instead_of_silently_using_ephemeral_storage():
    with pytest.raises(RuntimeError, match="requires DATABASE_URL to point to PostgreSQL"):
        configure_database_url("sqlite:///./solenos.db", railway_runtime=True)


def test_railway_postgres_requires_tls():
    result = configure_database_url(
        "postgresql://user:secret@db.example:5432/solenos",
        railway_runtime=True,
    )
    assert result.startswith("postgresql+psycopg2://")
    assert "sslmode=require" in result
    assert "secret" in result


def test_existing_verified_tls_setting_is_preserved():
    result = configure_database_url(
        "postgresql://user:secret@db.example:5432/solenos?sslmode=verify-full",
        railway_runtime=True,
    )
    assert "sslmode=verify-full" in result


def test_local_sqlite_remains_available_for_development():
    assert configure_database_url("sqlite:///./solenos.db", railway_runtime=False) == (
        "sqlite:///./solenos.db"
    )
