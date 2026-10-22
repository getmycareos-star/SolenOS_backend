"""Database setup with model registration deferred until first use."""
from threading import Lock
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()


def normalize_database_url(url: str) -> str:
    """Normalize Railway/Heroku PostgreSQL URLs to the installed psycopg 3 driver."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def configure_database_url(url: str, *, railway_runtime: bool) -> str:
    """Reject ephemeral SQLite storage in Railway so care records cannot silently disappear."""
    normalized = normalize_database_url(url.strip())
    if railway_runtime and not normalized:
        raise RuntimeError(
            "DATABASE_URL must be configured in Railway with a PostgreSQL connection string."
        )
    if railway_runtime and not normalized.startswith("postgresql+psycopg://"):
        raise RuntimeError(
            "DATABASE_URL must use PostgreSQL with the psycopg driver in Railway; "
            "SQLite and other database drivers are not supported for production care storage."
        )
    if railway_runtime:
        parsed = make_url(normalized)
        sslmode = parsed.query.get("sslmode")
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            parsed = parsed.update_query_dict({"sslmode": "require"})
        normalized = parsed.render_as_string(hide_password=False)
    return normalized


railway_runtime = any(
    os.getenv(name)
    for name in ("RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME")
)
database_url = configure_database_url(settings.database_url, railway_runtime=railway_runtime)
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite:") else {}
engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
_initialized = False
_init_lock = Lock()


def _sqlite_column_type(col, dialect):
    try:
        return str(col.type.compile(dialect=dialect))
    except Exception:
        return "TEXT"


def ensure_database_schema(bind=None):
    """Add only missing nullable/defaulted columns to legacy SQLite tables.

    This legacy compatibility helper is not a substitute for reviewed migrations.
    It does not drop or rewrite existing data.
    """
    from sqlalchemy import inspect, text
    target = bind or engine
    # Runtime ALTER TABLE is retained only for the legacy SQLite development
    # database. PostgreSQL schema changes must be applied through Alembic.
    try:
        dialect = target.dialect
        dialect_name = dialect.name
    except Exception as exc:
        raise RuntimeError(
            "Unable to inspect the configured database schema; refusing to start with unverified care storage."
        ) from exc
    if dialect_name != "sqlite":
        return
    try:
        insp = inspect(target)
    except Exception as exc:
        raise RuntimeError(
            "Unable to inspect the configured database schema; refusing to start with unverified care storage."
        ) from exc
    for table_name, table in Base.metadata.tables.items():
        if not insp.has_table(table_name):
            continue
        existing = {c["name"] for c in (insp.get_columns(table_name) or [])}
        for col in table.columns:
            if col.name in existing:
                continue
            col_type = _sqlite_column_type(col, dialect) or "TEXT"
            nullable = "" if col.nullable else " NOT NULL"
            default = ""
            if col.server_default is not None:
                default = f" DEFAULT {col.server_default.arg}"
            elif col.default is not None and not callable(col.default):
                v = col.default
                if isinstance(v, bool):
                    default = f" DEFAULT {1 if v else 0}"
                elif isinstance(v, (int, float)):
                    default = f" DEFAULT {v}"
                elif isinstance(v, str):
                    default = f" DEFAULT '{v}'"
            stmt = text(f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {col_type}{default}{nullable}')
            try:
                with target.begin() as conn:
                    conn.execute(stmt)
            except Exception as exc:
                # A concurrent initializer may have added this column. Otherwise,
                # fail startup instead of silently serving against an incomplete schema.
                try:
                    refreshed = {
                        column["name"]
                        for column in inspect(target).get_columns(table_name)
                    }
                except Exception as inspect_exc:
                    raise RuntimeError(
                        f"Unable to verify database schema after adding {table_name}.{col.name}."
                    ) from inspect_exc
                if col.name not in refreshed:
                    raise RuntimeError(
                        f"Database schema is missing required column {table_name}.{col.name}; "
                        "automatic compatibility update failed. Apply a reviewed migration "
                        "before starting SolenOS."
                    ) from exc
                existing = refreshed



def _register_all_models():
    """Ensure schema checks include every model, independent of import order."""
    import app.models.care  # noqa: F401
    import app.models.uncertainty  # noqa: F401
    import app.models.temporal  # noqa: F401
    import app.models.change  # noqa: F401
    import app.models.significance  # noqa: F401
    import app.models.action  # noqa: F401
    import app.models.caregiver_intelligence  # noqa: F401
    import app.models.causal  # noqa: F401
    import app.models.memory  # noqa: F401
    import app.models.time_location  # noqa: F401
    import app.models.safety  # noqa: F401
    import app.models.auth_identity  # noqa: F401
    import app.models.capture  # noqa: F401
    import app.models.collaboration  # noqa: F401


def validate_database_schema(bind=None):
    """Fail closed when any registered ORM table or column is missing.

    PostgreSQL production schema is migration-owned: this check must not
    silently treat a missing table as healthy or let startup create it outside
    Alembic. SQLite development retains create_all() for local convenience.
    """
    from sqlalchemy import inspect

    _register_all_models()
    target = bind or engine
    inspector = inspect(target)
    missing = []
    existing_tables = set(inspector.get_table_names())
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            missing.append(f"{table_name} (table missing)")
            continue
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        absent = sorted(column.name for column in table.columns if column.name not in actual)
        if absent:
            missing.append(f"{table_name}: {', '.join(absent)}")
    if missing:
        details = "; ".join(missing[:30])
        if len(missing) > 30:
            details += f"; and {len(missing) - 30} additional schema differences"
        raise RuntimeError(
            "Database schema is behind the application models. Apply reviewed Alembic "
            f"migrations before serving care workflows. Missing schema elements: {details}"
        )


def init_db():
    """Validate production schemas against migrations; create local SQLite schema once."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        _register_all_models()
        if engine.dialect.name == "sqlite":
            Base.metadata.create_all(bind=engine)
            ensure_database_schema()
        elif engine.dialect.name != "postgresql":
            raise RuntimeError(
                f"Unsupported database dialect '{engine.dialect.name}'; "
                "SolenOS supports PostgreSQL in production and SQLite for local development."
            )
        # Do not run create_all() against PostgreSQL: schema changes there must
        # be owned by reviewed Alembic migrations, not silently created at request time.
        validate_database_schema(engine)
        _initialized = True


def get_db():
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
