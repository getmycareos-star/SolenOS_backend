"""Database setup with model registration deferred until first use."""
from threading import Lock
import os

from sqlalchemy import create_engine
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
    normalized = normalize_database_url(url)
    if railway_runtime and normalized.startswith("sqlite:"):
        raise RuntimeError(
            "DATABASE_URL must use PostgreSQL in Railway; SQLite storage is not durable across deployments."
        )
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



def validate_database_schema(bind=None):
    """Fail closed when an existing table is missing ORM-declared columns.

    create_all() can create absent tables but cannot migrate columns on tables
    that already exist. Report that drift explicitly instead of letting the
    service return a false-positive healthy status and fail later on real care
    workflows.
    """
    from sqlalchemy import inspect

    target = bind or engine
    inspector = inspect(target)
    missing = []
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            # Missing tables are handled by create_all() before validation.
            continue
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        absent = sorted(column.name for column in table.columns if column.name not in actual)
        if absent:
            missing.append(f"{table_name}: {', '.join(absent)}")
    if missing:
        details = "; ".join(missing)
        raise RuntimeError(
            "Database schema is behind the application models. Apply reviewed Alembic "
            f"migrations before serving care workflows. Missing columns: {details}"
        )


def init_db():
    """Register all models before schema creation; run once per process."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
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
        Base.metadata.create_all(bind=engine)
        ensure_database_schema()
        validate_database_schema(engine)
        _initialized = True


def get_db():
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
