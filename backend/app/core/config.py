import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SolenOS"
    debug: bool = False
    database_url: str = "sqlite:///./solenos.db"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def validate_production_configuration(self) -> None:
        environment_values = [
            os.getenv("RAILWAY_ENVIRONMENT_NAME", ""),
            os.getenv("RAILWAY_ENVIRONMENT", ""),
            os.getenv("APP_ENV", ""),
            os.getenv("ENVIRONMENT", ""),
        ]
        is_production = any(value.strip().lower() in {"production", "prod"} for value in environment_values)
        if not is_production:
            return

        if not self.database_url.lower().startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
            raise RuntimeError("Production must use managed PostgreSQL; local SQLite is not durable on Railway.")
        if (
            not self.secret_key
            or self.secret_key == "change-me-in-production"
            or len(self.secret_key) < 32
        ):
            raise RuntimeError("Production SECRET_KEY must be a unique random value of at least 32 characters.")
        if self.debug:
            raise RuntimeError("Production DEBUG must be false.")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production_configuration()
    return settings
