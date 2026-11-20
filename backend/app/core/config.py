import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SolenOS"
    # Debug mode is opt-in; never default production to debug output.
    debug: bool = False
    # SQLite is a local-development fallback only. Hosted runtimes must use
    # managed PostgreSQL so a missing DATABASE_URL cannot create ephemeral data.
    database_url: str = "sqlite:///./solenos.db"
    secret_key: str = ""
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:support@solenosai.net"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def is_railway_runtime(self) -> bool:
        # Match database_config.is_railway_runtime: Railway always supplies a
        # project identifier, while environment labels may be absent in some
        # local/test harnesses and older deployment configurations.
        return any(
            os.getenv(name, "").strip()
            for name in (
                "RAILWAY_PROJECT_ID",
                "RAILWAY_ENVIRONMENT",
                "RAILWAY_ENVIRONMENT_NAME",
            )
        )

    @property
    def is_production(self) -> bool:
        environment = (
            os.getenv("RAILWAY_ENVIRONMENT_NAME")
            or os.getenv("RAILWAY_ENVIRONMENT")
            or os.getenv("APP_ENV")
            or os.getenv("ENVIRONMENT")
            or ""
        )
        return environment.strip().lower() in {"production", "prod"}

    def validate_production_configuration(self) -> None:
        # Every Railway runtime is hosted and must have a durable database and
        # non-default signing key. Explicit production environments receive the
        # same checks even when hosted outside Railway.
        if not (self.is_railway_runtime or self.is_production):
            return

        database_url = self.database_url.strip().lower()
        if not database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg2://")):
            raise RuntimeError(
                "Production/Railway runtime requires DATABASE_URL to point to managed PostgreSQL; "
                "SQLite and missing DATABASE_URL values are not allowed."
            )

        if self.secret_key == "change-me-in-production" or len(self.secret_key.strip()) < 32:
            raise RuntimeError(
                "Production/Railway runtime requires SECRET_KEY to be a unique random value "
                "of at least 32 characters."
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production_configuration()
    return settings
