from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import get_settings

settings = get_settings()
database_url = settings.database_url

# Railway/Supabase commonly provide postgres:// or postgresql:// URLs. Use
# psycopg 3 explicitly so the connection does not depend on an implicit driver.
if database_url.startswith("postgres://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
elif database_url.startswith("postgresql://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]

parsed_url = make_url(database_url)
engine_options = {"pool_pre_ping": True}
if parsed_url.get_backend_name() == "sqlite":
    engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(database_url, **engine_options)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Import every model module before create_all so all application tables are
# registered in Base.metadata, regardless of import order.
import app.models.care  # noqa: E402, F401
import app.models.memory  # noqa: E402, F401
import app.models.time_location  # noqa: E402, F401
import app.models.uncertainty  # noqa: E402, F401
import app.models.temporal  # noqa: E402, F401
import app.models.change  # noqa: E402, F401
import app.models.significance  # noqa: E402, F401
import app.models.action  # noqa: E402, F401
import app.models.caregiver_intelligence  # noqa: E402, F401
import app.models.causal  # noqa: E402, F401

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
    """Add missing nullable/defaulted ORM columns without dropping existing data.

    This is a legacy compatibility guard, not a substitute for versioned database
    migrations. Production schema changes should be delivered through Alembic.
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
            # Adding a non-null column without a server-side default can fail for
            # existing rows. Do not silently attempt an unsafe schema mutation.
            if not col.nullable and col.server_default is None and col.default is None:
                continue
            col_type = _sqlite_column_type(col, dialect) or "TEXT"
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
                    default = " DEFAULT '" + v.replace("'", "''") + "'"
            stmt = text(f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {col_type}{default}')
            try:
                with target.begin() as conn:
                    conn.execute(stmt)
            except Exception:
                # Keep startup from masking the exact database error; health and
                # actual workflow checks must still establish readiness.
                continue


ensure_database_schema()
