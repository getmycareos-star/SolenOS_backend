from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.database import Base, engine

# Import every model before Alembic reads metadata.
import app.models.care  # noqa: F401
import app.models.uncertainty  # noqa: F401
import app.models.temporal  # noqa: F401
import app.models.change  # noqa: F401
import app.models.significance  # noqa: F401
import app.models.action  # noqa: F401
import app.models.caregiver_intelligence  # noqa: F401
import app.models.causal  # noqa: F401
import app.models.memory  # noqa: F401
import app.models.time_location  # noqa: F401
import app.models.safety  # noqa: F401
import app.models.auth_identity  # noqa: F401
import app.models.capture  # noqa: F401
import app.models.collaboration  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
