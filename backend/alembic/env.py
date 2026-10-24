from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.core.database import Base
from app.core.database_config import configure_database_url, is_railway_runtime

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
database_url = configure_database_url(
    settings.database_url,
    railway_runtime=(
        is_railway_runtime()
        or settings.database_url.strip().startswith(
            ("postgres://", "postgresql://", "postgresql+psycopg2://")
        )
    ),
)
# Enforce Supabase Session Pooler username here as well: Railway pre-deploy
# must never attempt authentication as the unqualified "postgres" user.
_parsed_database_url = make_url(database_url)
if (
    _parsed_database_url.host
    and _parsed_database_url.host.endswith(".pooler.supabase.com")
    and _parsed_database_url.username == "postgres"
):
    _parsed_database_url = _parsed_database_url.set(
        username="postgres.qyialgkfbwralolryhjx"
    )
database_url = _parsed_database_url.render_as_string(hide_password=False)
print(
    "Alembic database target:",
    f"{_parsed_database_url.host}:{_parsed_database_url.port or 5432}",
    f"user={_parsed_database_url.username}",
)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    # Use the already-normalized URL directly. engine_from_config can re-read
    # an unqualified URL from alembic.ini in some deployment contexts.
    connectable = create_engine(
        database_url,
        poolclass=pool.NullPool,
        pool_pre_ping=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
