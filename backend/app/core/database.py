from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database_config import configure_database_url, is_railway_runtime

settings = get_settings()
RAILWAY_RUNTIME = is_railway_runtime()
database_url = configure_database_url(settings.database_url, railway_runtime=RAILWAY_RUNTIME)

# SQLite-only connection arguments must not leak into PostgreSQL.
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Register all ORM models before local schema creation or Alembic autogeneration.
import app.models.care  # noqa: E402, F401
import app.models.anonymous_session  # noqa: E402, F401
import app.models.account  # noqa: E402, F401
import app.models.professional_account  # noqa: E402, F401
import app.models.interaction_session  # noqa: E402, F401
import app.models.memory  # noqa: E402, F401
import app.models.time_location  # noqa: E402, F401
import app.models.temporal  # noqa: E402, F401
import app.models.uncertainty  # noqa: E402, F401
import app.models.change  # noqa: E402, F401
import app.models.significance  # noqa: E402, F401
import app.models.action  # noqa: E402, F401
import app.models.caregiver_intelligence  # noqa: E402, F401
import app.models.causal  # noqa: E402, F401
import app.models.collaboration  # noqa: E402, F401
import app.models.care_continuity  # noqa: E402, F401
import app.models.safety  # noqa: E402, F401
import app.models.reminder  # noqa: E402, F401


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
    """Add compatible missing columns to local SQLite only; never mutate production PostgreSQL."""
    from sqlalchemy import inspect, text

    target = bind or engine
    if target.dialect.name != "sqlite":
        return

    inspector = inspect(target)
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for col in table.columns:
            if col.name in existing:
                continue
            col_type = _sqlite_column_type(col, target.dialect) or "TEXT"
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
                    default = " DEFAULT '" + value.replace("'", "''") + "'"
            nullable = "" if col.nullable else " NOT NULL"
            statement = text(
                f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" '
                f"{col_type}{default}{nullable}"
            )
            try:
                with target.begin() as connection:
                    connection.execute(statement)
            except Exception:
                # Concurrent initializers are acceptable only if the column now exists.
                refreshed = {
                    column["name"] for column in inspect(target).get_columns(table_name)
                }
                if col.name not in refreshed:
                    raise RuntimeError(
                        f"Local SQLite schema update failed for {table_name}.{col.name}"
                    )


def database_schema_differences(bind=None):
    """Return missing ORM tables/columns without reading or exposing care records."""
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
            column.name for column in table.columns if column.name not in existing_columns
        )
        if missing_columns:
            differences.append(f"{table_name}: missing columns {', '.join(missing_columns)}")
    return differences


# Local development keeps its low-friction SQLite bootstrap. Railway production
# must run reviewed Alembic migrations before ASGI startup; create_all() cannot
# alter existing PostgreSQL tables and must not silently mutate production schema.
if not RAILWAY_RUNTIME:
    Base.metadata.create_all(bind=engine)
    ensure_database_schema()
