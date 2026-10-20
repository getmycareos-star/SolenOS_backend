from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from app.core.config import get_settings
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

settings = get_settings()

# SQLite's thread option is not accepted by PostgreSQL/MySQL drivers. Keep
# engine options dialect-specific so DATABASE_URL can select a production DB.
_connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite:")
    else {}
)
engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Database connection metrics
try:
    from app.core.metrics import DATABASE_CONNECTIONS_ACTIVE
    # Event listeners for connection pool
    @event.listens_for(engine, "connect")
    def receive_connect(dbapi_connection, connection_record):
        """Increment active connection count when a new connection is created."""
        DATABASE_CONNECTIONS_ACTIVE.inc()
        logger.debug("Database connection created")

    @event.listens_for(engine, "close")
    def receive_close(dbapi_connection, connection_record):
        """Decrement active connection count when a connection is closed."""
        DATABASE_CONNECTIONS_ACTIVE.dec()
        logger.debug("Database connection closed")
        
    @event.listens_for(engine, "checkout")
    def receive_checkout(dbapi_connection, connection_record, connection_proxy):
        """Increment active connection count when a connection is checked out from pool."""
        pass  # Already counted in connect event
        
    @event.listens_for(engine, "checkin")
    def receive_checkin(dbapi_connection, connection_record):
        """Decrement active connection count when a connection is returned to pool."""
        pass  # Already counted in close event
        
except ImportError:
    # Metrics module not available, skip monitoring
    logger.warning("Metrics module not available, database connection monitoring disabled")

def utc_now():
    return datetime.now(timezone.utc)

import app.models.document
import app.models.uncertainty
import app.models.temporal
import app.models.change
import app.models.significance
import app.models.action
import app.models.caregiver_intelligence
import app.models.caregiver
import app.models.causal
import app.models.safety
import app.models.memory
import app.models.time_location
import app.models.prioritization
import app.models.meaning
import app.models.knowledge_revision
import app.models.llm
import app.models.person_context
import app.models.continuity
import app.models.what_matters
import app.models.trajectory
import app.models.behavior
import app.models.contradiction
import app.models.care_planning
import app.models.follow_up
import app.models.conversation
import app.models.wellbeing
import app.models.care_sharing
import app.models.community
import app.models.care_circle
import app.models.identity
import app.models.flexible_document
import app.models.care
import app.models.care_event
import app.models.evidence
import app.models.notification
import app.models.situation_information
import app.models.job  # Add this line

_initialized = False


def init_db():
    """Create tables and backfill missing columns. Safe to call repeatedly.

    Called from the FastAPI startup event so importing this module (which
    happens at app-construction time) no longer triggers a synchronous
    SQLite round-trip.
    """
    global _initialized
    if _initialized:
        return
    Base.metadata.create_all(bind=engine)

    _initialized = True


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

    from sqlalchemy import text as _text
    new_tables = [
        (name, table) for name, table in Base.metadata.tables.items()
        if not insp.has_table(name)
    ]
    if new_tables:
        try:
            Base.metadata.create_all(bind=target)
        except Exception:
            pass

    for table_name, table in Base.metadata.tables.items():
        if not insp.has_table(table_name):
            continue  # create_all already handles table creation; this guard adds columns only
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
                pass


ensure_database_schema()

