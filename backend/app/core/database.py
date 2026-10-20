"""Database setup with model registration deferred until first use."""
from threading import Lock

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
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
    try:
        insp = inspect(target)
        dialect = target.dialect
    except Exception:
        return
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
            except Exception:
                # Preserve the existing legacy behavior; schema verification must
                # surface unresolved missing columns before production rollout.
                pass


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
        Base.metadata.create_all(bind=engine)
        ensure_database_schema()
        _initialized = True


def get_db():
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
