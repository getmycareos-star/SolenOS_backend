from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, inspect, pool, text

from app.core.config import get_settings
from app.core.database import Base
from app.core.database_config import configure_database_url, is_railway_runtime

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
database_url = configure_database_url(settings.database_url, railway_runtime=is_railway_runtime())
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
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        pool_pre_ping=True,
    )
    with connectable.connect() as connection:
        # Alembic's default version_num column is VARCHAR(32), but this
        # repository has canonical revision identifiers longer than 32 chars.
        # Widen/create the bookkeeping table before Alembic attempts to stamp
        # those revisions. This changes migration metadata only, not care data.
        if connection.dialect.name == "postgresql":
            if inspect(connection).has_table("alembic_version"):
                connection.execute(text(
                    "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)"
                ))
            else:
                connection.execute(text(
                    "CREATE TABLE alembic_version ("
                    "version_num VARCHAR(128) NOT NULL, "
                    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                ))
            connection.commit()
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
