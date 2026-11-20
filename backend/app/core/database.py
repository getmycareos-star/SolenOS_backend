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
import app.models.caregiver_account  # noqa: E402, F401
import app.models.identity_security  # noqa: E402, F401
import app.models.interaction_session  # noqa: E402, F401
import app.models.memory  # noqa: E402, F401
import app.models.time_location  # noqa: E402, F401
import app.models.temporal  # noqa: E402, F401
import app.models.uncertainty  # noqa: E402, F401
import app.models.change  # noqa: E402, F401
import app.models.observation_coverage  # noqa: E402, F401
import app.models.significance  # noqa: E402, F401
import app.models.action  # noqa: E402, F401
import app.models.caregiver_intelligence  # noqa: E402, F401
import app.models.causal  # noqa: E402, F401
import app.models.collaboration  # noqa: E402, F401
import app.models.notifications  # noqa: E402, F401
import app.models.care_continuity  # noqa: E402, F401
import app.models.safety  # noqa: E402, F401
import app.models.reminder  # noqa: E402, F401
import app.models.capture_analysis  # noqa: E402, F401
import app.models.privacy_delivery  # noqa: E402, F401
import app.models.privacy_request  # noqa: E402, F401
import app.models.privacy_export  # noqa: E402, F401


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
    """Compare ORM-owned tables, columns, indexes, unique constraints and foreign keys."""
    from sqlalchemy import inspect

    target = bind or engine
    inspector = inspect(target)
    existing_tables = set(inspector.get_table_names())
    differences = []

    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            differences.append(f"{table_name} (table missing)")
            continue

        reflected_columns = {
            column["name"]: column for column in inspector.get_columns(table_name)
        }
        missing_columns = sorted(
            column.name for column in table.columns if column.name not in reflected_columns
        )
        if missing_columns:
            differences.append(f"{table_name}: missing columns {', '.join(missing_columns)}")

        def type_contract(value):
            affinity = getattr(value, "_type_affinity", type(value))
            return (
                getattr(affinity, "__name__", str(affinity)),
                getattr(value, "length", None),
                getattr(value, "precision", None),
                getattr(value, "scale", None),
                getattr(value, "timezone", None),
            )

        type_mismatches = sorted(
            column.name
            for column in table.columns
            if column.name in reflected_columns
            and type_contract(column.type) != type_contract(reflected_columns[column.name]["type"])
        )
        if type_mismatches:
            differences.append(
                f"{table_name}: data types differ for {', '.join(type_mismatches)}"
            )

        expected_primary_key = tuple(column.name for column in table.primary_key.columns)
        actual_primary_key = tuple(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
        if expected_primary_key != actual_primary_key:
            differences.append(
                f"{table_name}: primary key differs (expected {expected_primary_key}, actual {actual_primary_key})"
            )

        # Nullability is part of the persisted data contract. Do not silently
        # accept a nullable production column where the ORM requires a value.
        nullability_mismatches = sorted(
            column.name
            for column in table.columns
            if column.name in reflected_columns
            and bool(reflected_columns[column.name].get("nullable", True)) != bool(column.nullable)
        )
        if nullability_mismatches:
            differences.append(
                f"{table_name}: nullability differs for {', '.join(nullability_mismatches)}"
            )

        expected_foreign_keys = set()
        for constraint in table.foreign_key_constraints:
            local_columns = tuple(element.parent.name for element in constraint.elements)
            referred_table = next(iter(constraint.elements)).column.table.name
            referred_columns = tuple(element.column.name for element in constraint.elements)
            expected_foreign_keys.add((local_columns, referred_table, referred_columns))
        actual_foreign_keys = {
            (
                tuple(item.get("constrained_columns") or []),
                item.get("referred_table"),
                tuple(item.get("referred_columns") or []),
            )
            for item in inspector.get_foreign_keys(table_name)
        }
        missing_foreign_keys = expected_foreign_keys - actual_foreign_keys
        if missing_foreign_keys:
            differences.append(
                f"{table_name}: missing foreign keys "
                + "; ".join(
                    f"{local} -> {remote_table}({remote})"
                    for local, remote_table, remote in sorted(missing_foreign_keys)
                )
            )

        expected_unique_constraints = {
            tuple(sorted(column.name for column in constraint.columns))
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        actual_unique_constraints = {
            tuple(item.get("column_names") or [])
            for item in inspector.get_unique_constraints(table_name)
        }
        missing_unique_constraints = expected_unique_constraints - actual_unique_constraints
        if missing_unique_constraints:
            differences.append(
                f"{table_name}: missing unique constraints "
                + "; ".join(", ".join(columns) for columns in sorted(missing_unique_constraints))
            )

        expected_indexes = set()
        for index in table.indexes:
            columns = tuple(
                sorted(expression.name for expression in index.expressions if hasattr(expression, "name"))
            )
            if columns and len(columns) == len(index.expressions):
                expected_indexes.add((columns, bool(index.unique)))
        actual_indexes = {
            (
                tuple(sorted(item.get("column_names") or [])),
                bool(item.get("unique", False)),
            )
            for item in inspector.get_indexes(table_name)
            if item.get("column_names")
        }
        missing_indexes = expected_indexes - actual_indexes
        if missing_indexes:
            differences.append(
                f"{table_name}: missing indexes "
                + "; ".join(
                    f"{columns} (unique={unique})"
                    for columns, unique in sorted(missing_indexes)
                )
            )

    return differences


# Local development keeps its low-friction SQLite bootstrap. Railway production
# must run reviewed Alembic migrations before ASGI startup; create_all() cannot
# alter existing PostgreSQL tables and must not silently mutate production schema.
import app.models.qwen_background_job  # register Qwen jobs before local create_all
import app.models.improvement_training_example  # register consent-gated improvement examples
import app.models.privacy_delivery  # register privacy and handoff lifecycle tables
import app.models.appointment_workspace  # register durable appointment workflow records before local schema bootstrap
import app.models.open_loop  # register persistent open-loop tables without importing API routes
import app.models.ai_output_governance  # register shared AI proposal and decision records
import app.models.caregiver_access  # register caregiver membership, invitation and transfer tables before local bootstrap


# Metadata bootstrap is strictly a local SQLite convenience. Running Alembic
# against PostgreSQL imports this module to load model metadata; create_all()
# there would pre-create new tables before their reviewed migrations execute.
if engine.dialect.name == "sqlite" and not RAILWAY_RUNTIME:
    Base.metadata.create_all(bind=engine)
    ensure_database_schema()
