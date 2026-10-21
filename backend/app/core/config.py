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
    cors_origins: str = "http://localhost:5173,http://localhost:3000,https://solenosai.netlify.app"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def is_production(self) -> bool:
        environment = (
            os.getenv("RAILWAY_ENVIRONMENT")
            or os.getenv("APP_ENV")
            or os.getenv("ENVIRONMENT")
            or ""
        )
        return environment.strip().lower() in {"production", "prod"}

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def validate_production_configuration(self) -> None:
        if not self.is_production:
            return
        if self.database_url.startswith("sqlite:"):
            raise RuntimeError(
                "Production requires DATABASE_URL to point to the managed PostgreSQL database; "
                "SQLite is only the local-development fallback."
            )
        if self.secret_key == "change-me-in-production" or len(self.secret_key) < 32:
            raise RuntimeError(
                "Production requires SECRET_KEY to be set to a unique random value of at least 32 characters."
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production_configuration()
    return settings
