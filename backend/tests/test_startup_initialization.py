"""Startup regression tests for database schema initialization."""
import asyncio
from contextlib import AbstractContextManager
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool


def test_init_db_creates_required_tables_on_configured_engine(monkeypatch):
    """The schema initializer creates the tables used by core care workflows."""
    import app.core.database as database

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(database, "_initialized", False)

    try:
        database.init_db()
        tables = set(inspect(test_engine).get_table_names())
        assert "persons" in tables
        assert "vital_signs" in tables
        assert "evidence" in tables
    finally:
        test_engine.dispose()


def test_lifespan_initializes_schema_before_connectivity_check(monkeypatch):
    """A SELECT 1 check must not run before schema initialization."""
    import importlib

    main = importlib.import_module("app.main")
    events = []

    class FakeConnection(AbstractContextManager):
        def execute(self, statement):
            events.append("connectivity_query")

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeEngine:
        def connect(self):
            events.append("connect")
            return FakeConnection()

    monkeypatch.setattr(main, "engine", FakeEngine())
    monkeypatch.setattr(main, "init_db", lambda: events.append("init_db"))
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

    asyncio.run(run_lifespan())
    assert events.index("init_db") < events.index("connect")
    assert "connectivity_query" in events
