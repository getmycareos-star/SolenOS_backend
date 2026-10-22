import pytest

from seed import _assert_demo_seed_allowed


def clear_environment(monkeypatch):
    for key in (
        "RAILWAY_ENVIRONMENT_NAME",
        "RAILWAY_ENVIRONMENT",
        "APP_ENV",
        "ENVIRONMENT",
        "SOLENOS_ENABLE_DEMO_SEED",
    ):
        monkeypatch.delenv(key, raising=False)


def test_demo_seed_is_disabled_by_default(monkeypatch):
    clear_environment(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")

    with pytest.raises(RuntimeError, match="disabled by default"):
        _assert_demo_seed_allowed()


def test_demo_seed_is_denied_when_environment_is_not_explicit(monkeypatch):
    clear_environment(monkeypatch)
    monkeypatch.setenv("SOLENOS_ENABLE_DEMO_SEED", "true")

    with pytest.raises(RuntimeError, match="explicitly named local development environment"):
        _assert_demo_seed_allowed()


def test_demo_seed_requires_explicit_local_opt_in(monkeypatch):
    clear_environment(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SOLENOS_ENABLE_DEMO_SEED", "true")

    _assert_demo_seed_allowed()


@pytest.mark.parametrize("environment_key", [
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_ENVIRONMENT",
    "APP_ENV",
    "ENVIRONMENT",
])
def test_demo_seed_is_forbidden_in_production_even_with_opt_in(monkeypatch, environment_key):
    clear_environment(monkeypatch)
    monkeypatch.setenv(environment_key, "production")
    monkeypatch.setenv("SOLENOS_ENABLE_DEMO_SEED", "true")

    with pytest.raises(RuntimeError, match="never be seeded in production"):
        _assert_demo_seed_allowed()


def test_production_environment_cannot_be_overridden_by_development_label(monkeypatch):
    clear_environment(monkeypatch)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SOLENOS_ENABLE_DEMO_SEED", "true")

    with pytest.raises(RuntimeError, match="never be seeded in production"):
        _assert_demo_seed_allowed()


def test_unknown_environment_label_blocks_demo_seed(monkeypatch):
    clear_environment(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("SOLENOS_ENABLE_DEMO_SEED", "true")

    with pytest.raises(RuntimeError, match="explicitly named local development environment"):
        _assert_demo_seed_allowed()
