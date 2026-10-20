"""Startup regression tests for database schema initialization."""
import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool


def test_lifespan_initializes_required_tables_before_serving(monkeypatch):
    """A successful startup must initialize the schema on its configured engine."""
    import app.core.database as database
    import importlib

    main = importlib.import_module("app.main")
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(main, "engine", test_engine)
    monkeypatch.setattr(database, "_initialized", False)
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            debug=True,
            app_name="SolenOS startup test",
            version="test",
            secret_key="test-only",
            backup_interval_hours=0,
            data_deletion_enabled=False,
            data_deletion_interval_hours=0,
        ),
    )

    async def noop():
        return None

    monkeypatch.setattr(main.cache_service, "connect", noop)
    monkeypatch.setattr(main.cache_service, "disconnect", noop)
    monkeypatch.setattr(main, "init_app_info", lambda **kwargs: None)
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    async def run_lifespan():
        async with main.lifespan(main.app):
            pass

    try:
        asyncio.run(run_lifespan())
        tables = set(inspect(test_engine).get_table_names())
        assert "persons" in tables
        assert "vital_signs" in tables
        assert "evidence" in tables
    finally:
        test_engine.dispose()
