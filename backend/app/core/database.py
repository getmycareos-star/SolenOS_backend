from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()


def normalize_database_url(raw_url: str, *, require_ssl: bool = False) -> str:
    """Normalize hosted PostgreSQL URLs to the installed psycopg2 driver.

    Supabase and Railway commonly supply postgres:// or postgresql:// URLs.
    Production PostgreSQL connections require TLS unless the URL explicitly
    configures an sslmode.
    """
    value = raw_url.strip()
    if value.startswith("postgres://"):
        value = "postgresql+psycopg2://" + value[len("postgres://"):]
    elif value.startswith("postgresql://"):
        value = "postgresql+psycopg2://" + value[len("postgresql://"):]
    elif value.startswith("postgresql+psycopg://"):
        # This project installs psycopg2-binary, not psycopg 3.
        value = "postgresql+psycopg2://" + value[len("postgresql+psycopg://"):]

    parsed = make_url(value)
    if parsed.get_backend_name() == "postgresql" and require_ssl and "sslmode" not in parsed.query:
        parsed = parsed.update_query_dict({"sslmode": "require"})
    return parsed.render_as_string(hide_password=False)


database_url = normalize_database_url(
    settings.database_url,
    require_ssl=settings.is_production,
)
parsed_url = make_url(database_url)
engine_options = {"pool_pre_ping": True}
if parsed_url.get_backend_name() == "sqlite":
    engine_options["connect_args"] = {"check_same_thread": False}
engine = create_engine(database_url, **engine_options)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Register every ORM model before create_all. Importing app.api.v1 imports this
# module before it imports app.models.care, so omitting care here left foreign
# keys such as information_gaps.created_by_caregiver_id unresolved at startup.
import app.models.care  # noqa: E402, F401
import app.models.memory  # noqa: E402, F401
import app.models.time_location  # noqa: E402, F401
import app.models.temporal  # noqa: E402, F401
import app.models.uncertainty  # noqa: E402, F401
import app.models.change  # noqa: E402, F401
import app.models.significance  # noqa: E402, F401
import app.models.action  # noqa: E402, F401
import app.models.caregiver_intelligence  # noqa: E402, F401
import app.models.causal  # noqa: E402, F401
import app.models.safety  # noqa: E402, F401
import app.models.collaboration  # noqa: E402, F401
import app.models.auth  # noqa: E402, F401

Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _sqlite_column_type(col, dialect):
    try:
        return str(col.type.compile(dialect=dialect))
    except Exception:
        return "TEXT"


def ensure_database_schema(bind=None):
    """Idempotently add ORM columns that are missing from existing SQLite tables.

    The backend persists via Base.metadata.create_all only (no Alembic in use).
    create_all creates tables but cannot add columns to tables that already
    exist with an older shape, which previously surfaced as HTTP 500
    ("table changes has no column named ...") after a model change. This guard
    adds only missing columns and never drops or rewrites data, so it is safe to
    run at startup against a stale dev database.
    """
    target = bind or engine
    if target.dialect.name != "sqlite":
        # PostgreSQL schema changes must use explicit migrations. Do not run
        # best-effort ALTER TABLE statements during application import.
        return

    from sqlalchemy import inspect, text

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
                # A single column that cannot be added should not block startup;
                # it will raise concretely if actually needed.
                pass


ensure_database_schema()
