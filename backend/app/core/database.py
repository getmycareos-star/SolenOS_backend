from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.core.config import get_settings

settings = get_settings()

# SQLite needs check_same_thread=False; PostgreSQL drivers do not accept that
# SQLite-only connection argument.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Register every ORM model before create_all. Importing app.api.v1 imports this
# module before it imports app.models.care, so omitting care here left foreign
# keys such as information_gaps.created_by_caregiver_id unresolved at startup.
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
    from sqlalchemy import inspect, text

    target = bind or engine
    try:
        insp = inspect(target)
        dialect = target.dialect
    except Exception:
        return
    # Existing PostgreSQL deployments may retain the pre-anonymous NOT NULL
    # constraint. Remove only that constraint; never invent a placeholder email.
    if dialect.name == "postgresql" and insp.has_table("caregivers"):
        caregiver_columns = {c["name"]: c for c in (insp.get_columns("caregivers") or [])}
        email_column = caregiver_columns.get("email")
        if email_column and not email_column.get("nullable", True):
            with target.begin() as conn:
                conn.execute(text('ALTER TABLE "caregivers" ALTER COLUMN "email" DROP NOT NULL'))

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
