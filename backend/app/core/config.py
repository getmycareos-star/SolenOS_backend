import ipaddress
import os
from urllib.parse import urlparse
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SolenOS"
    # Debug mode is opt-in; never default production to debug output.
    debug: bool = False
    # SQLite is a local-development fallback only. Production validation below
    # rejects it so a missing DATABASE_URL cannot silently create local storage.
    database_url: str = "sqlite:///./solenos.db"
    secret_key: str = ""
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    cors_origins: str = "http://localhost:5173,http://localhost:3000,https://solenosai.netlify.app"
    # AI requests are routed only to the configured private Ollama service; no hosted fallback.
    ollama_base_url: str = ""
    ollama_model: str = "qwen2.5:1.5b"
    ollama_timeout_seconds: float = 60.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def ollama_configured(self) -> bool:
        url = self.ollama_base_url.strip()
        model = self.ollama_model.strip()
        if not url or not model:
            return False
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
        except ValueError:
            return False
        if parsed.scheme != "http" or not host.endswith(".railway.internal"):
            return False
        if host == "localhost":
            return False
        try:
            if ipaddress.ip_address(host).is_loopback:
                return False
        except ValueError:
            pass
        return True

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
        if not self.is_production:
            return

        database_url = self.database_url.strip().lower()
        if not database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg2://")):
            raise RuntimeError(
                "Production requires DATABASE_URL to point to the managed PostgreSQL database; "
                "SQLite and missing DATABASE_URL values are not allowed."
            )

        if self.secret_key == "change-me-in-production" or len(self.secret_key.strip()) < 32:
            raise RuntimeError(
                "Production requires SECRET_KEY to be a unique random value of at least 32 characters."
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production_configuration()
    return settings
