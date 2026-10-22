import pytest

from app.core.config import Settings


def clear_environment(monkeypatch):
    for name in (
        "RAILWAY_ENVIRONMENT_NAME",
        "RAILWAY_ENVIRONMENT",
        "APP_ENV",
        "ENVIRONMENT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_development_allows_local_sqlite(monkeypatch):
    clear_environment(monkeypatch)
    settings = Settings(database_url="sqlite:///./solenos.db", secret_key="development-only")
    settings.validate_production_configuration()


def test_production_rejects_sqlite_database(monkeypatch):
    clear_environment(monkeypatch)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    settings = Settings(database_url="sqlite:///./solenos.db", secret_key="x" * 48)

    with pytest.raises(RuntimeError, match="managed PostgreSQL"):
        settings.validate_production_configuration()


def test_production_rejects_default_or_short_secret(monkeypatch):
    clear_environment(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    settings = Settings(
        database_url="postgresql://db.example.test/solenos",
        secret_key="change-me-in-production",
    )

    with pytest.raises(RuntimeError, match="unique random value"):
        settings.validate_production_configuration()


def test_production_accepts_postgres_and_long_secret(monkeypatch):
    clear_environment(monkeypatch)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    settings = Settings(
        database_url="postgresql://db.example.test/solenos",
        secret_key="a-unique-random-secret-value-with-more-than-32-characters",
    )

    settings.validate_production_configuration()



def test_any_production_environment_indicator_enables_production_validation(monkeypatch):
    clear_environment(monkeypatch)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.setenv("APP_ENV", "production")

    settings = Settings(
        database_url="sqlite:///./solenos.db",
        secret_key="a-unique-random-secret-value-with-more-than-32-characters",
    )
    assert settings.is_production is True
    with pytest.raises(RuntimeError, match="managed PostgreSQL"):
        settings.validate_production_configuration()


def test_production_rejects_debug_mode(monkeypatch):
    clear_environment(monkeypatch)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    settings = Settings(
        database_url="postgresql://db.example.test/solenos",
        secret_key="a-unique-random-secret-value-with-more-than-32-characters",
        debug=True,
    )

    with pytest.raises(RuntimeError, match="DEBUG must be false"):
        settings.validate_production_configuration()
