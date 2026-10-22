from sqlalchemy.engine import make_url

from app.core.database import ensure_database_schema, normalize_database_url


def test_supabase_postgres_url_uses_installed_driver():
    normalized = normalize_database_url(
        "postgresql://user:secret@db.example.test:5432/postgres"
    )
    parsed = make_url(normalized)

    assert parsed.drivername == "postgresql+psycopg2"
    assert parsed.host == "db.example.test"
    assert parsed.database == "postgres"
    assert parsed.password == "secret"


def test_production_postgres_url_requires_tls():
    normalized = normalize_database_url(
        "postgres://user:secret@db.example.test:5432/postgres",
        require_ssl=True,
    )
    parsed = make_url(normalized)

    assert parsed.drivername == "postgresql+psycopg2"
    assert parsed.query["sslmode"] == "require"


def test_production_overrides_explicit_insecure_sslmode():
    normalized = normalize_database_url(
        "postgresql://user:secret@db.example.test:5432/postgres?sslmode=disable",
        require_ssl=True,
    )
    parsed = make_url(normalized)

    assert parsed.query["sslmode"] == "require"


def test_nonproduction_preserves_explicit_sslmode():
    normalized = normalize_database_url(
        "postgresql://user:secret@db.example.test:5432/postgres?sslmode=disable"
    )
    parsed = make_url(normalized)

    assert parsed.query["sslmode"] == "disable"



def test_legacy_schema_repair_does_not_run_on_postgresql():
    class FakeDialect:
        name = "postgresql"

    class FakePostgresBind:
        dialect = FakeDialect()

    # A PostgreSQL bind must return before inspection or any best-effort DDL.
    ensure_database_schema(bind=FakePostgresBind())
