from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import get_settings

settings = get_settings()
database_url = settings.database_url.strip()
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

# Import every model before create_all so SQLAlchemy sees all foreign-key targets.
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
import app.models.auth  # noqa: E402, F401

Base.metadata.create_all(bind=engine)


def ensure_additive_schema():
    """Add only safe, non-FK columns; fail startup on schema changes needing a migration."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    dialect = engine.dialect
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            if column.foreign_keys:
                raise RuntimeError(
                    "Database migration required before startup: "
                    + table_name + "." + column.name + " is a missing foreign-key column."
                )
            if not column.nullable and column.server_default is None and column.default is None:
                raise RuntimeError(
                    "Database migration required before startup: "
                    + table_name + "." + column.name + " is required and has no safe default."
                )

            column_type = column.type.compile(dialect=dialect)
            default_sql = ""
            if column.server_default is not None:
                default_sql = " DEFAULT " + str(column.server_default.arg)
            elif column.default is not None and not callable(column.default.arg):
                literal_processor = column.type.literal_processor(dialect)
                if literal_processor is None:
                    raise RuntimeError(
                        "Database migration required before startup: cannot safely encode default for "
                        + table_name + "." + column.name
                    )
                default_sql = " DEFAULT " + literal_processor(column.default.arg)
            nullable_sql = "" if column.nullable else " NOT NULL"
            quote = dialect.identifier_preparer.quote
            statement = (
                "ALTER TABLE " + quote(table_name) + " ADD COLUMN " + quote(column.name)
                + " " + column_type + default_sql + nullable_sql
            )
            with engine.begin() as connection:
                connection.execute(text(statement))


ensure_additive_schema()


def apply_postgres_compatibility_migrations():
    """Apply narrowly scoped, idempotent compatibility changes for existing PostgreSQL databases."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        # Caregivers can support the same person across multiple care records.
        # Older deployments may have the former UNIQUE(email) constraint.
        constraints = connection.execute(text("""
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = to_regclass('caregivers')
              AND contype = 'u'
              AND pg_get_constraintdef(oid) = 'UNIQUE (email)'
        """)).scalars().all()
        for constraint_name in constraints:
            quoted_name = engine.dialect.identifier_preparer.quote(constraint_name)
            connection.execute(text(f"ALTER TABLE caregivers DROP CONSTRAINT {quoted_name}"))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_caregivers_email ON caregivers (email)"
        ))


apply_postgres_compatibility_migrations()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
