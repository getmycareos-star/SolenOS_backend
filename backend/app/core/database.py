from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database_config import configure_database_url, is_railway_runtime

settings = get_settings()
database_url = configure_database_url(
    settings.database_url,
    railway_runtime=is_railway_runtime(),
)

# SQLite needs check_same_thread=False; PostgreSQL drivers do not accept that
# SQLite-only connection argument. Railway is explicitly prohibited from using
# SQLite because its application filesystem is not durable database storage.
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Register every ORM model before create_all.
import app.models.care  # noqa: E402, F401
import app.models.anonymous_session  # noqa: E402, F401
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

# Keep table creation for compatibility with existing deployments. Production
# schema changes must be handled by reviewed migrations; create_all alone does
# not update existing tables and readiness checks now detect missing columns.
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
    """Repair old local SQLite schemas only; never mutate a PostgreSQL schema here."""
    from sqlalchemy import inspect, text

    target = bind or engine
    if target.dialect.name != "sqlite":
        return

    insp = inspect(target)
    dialect = target.dialect
    for table_name, table in Base.metadata.tables.items():
        if not insp.has_table(table_name):
            continue
        existing = {c["name"] for c in insp.get_columns(table_name)}
        for col in table.columns:
            if col.name in existing:
                continue
            col_type = _sqlite_column_type(col, dialect) or "TEXT"
            nullable = "" if col.nullable else " NOT NULL"
            default = ""
            if col.server_default is not None:
                default = f" DEFAULT {col.server_default.arg}"
            elif col.default is not None and not callable(col.default):
                value = col.default
                if isinstance(value, bool):
                    default = f" DEFAULT {1 if value else 0}"
                elif isinstance(value, (int, float)):
                    default = f" DEFAULT {value}"
                elif isinstance(value, str):
                    # Escape single quotes before interpolating a literal default.
                    escaped = value.replace("'", "''")
                    default = f" DEFAULT '{escaped}'"
            statement = text(
                f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" '
                f"{col_type}{default}{nullable}"
            )
            try:
                with target.begin() as conn:
                    conn.execute(statement)
            except Exception:
                # Permit a concurrent initializer only if the column now exists.
                refreshed = {
                    column["name"]
                    for column in inspect(target).get_columns(table_name)
                }
                if col.name not in refreshed:
                    raise RuntimeError(
                        f"Could not update local SQLite schema for {table_name}.{col.name}"
                    )


def database_schema_differences(bind=None):
    """Return missing tables/columns without reading or returning any care records."""
    from sqlalchemy import inspect

    target = bind or engine
    inspector = inspect(target)
    existing_tables = set(inspector.get_table_names())
    differences = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            differences.append(f"{table_name} (table missing)")
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        missing_columns = sorted(
            column.name for column in table.columns
            if column.name not in existing_columns
        )
        if missing_columns:
            differences.append(f"{table_name}: missing columns {', '.join(missing_columns)}")
    return differences


ensure_database_schema()
