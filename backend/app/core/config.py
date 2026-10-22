import ipaddress
import os
from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings


RAILWAY_RUNTIME_VARIABLES = (
    "RAILWAY_PROJECT_ID",
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_ENVIRONMENT_NAME",
)


def is_loopback_ollama_url(url: str) -> bool:
    """Return true when the configured endpoint points back at this container."""
    try:
        host = urlparse(url.strip()).hostname
    except ValueError:
        return True
    if not host:
        return True
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_railway_private_ollama_url(url: str) -> bool:
    """Accept only Railway's private-network hostname for Railway-to-Ollama traffic."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "http" and host.endswith(".railway.internal")


def validate_deployment_secret(secret_key: str, *, railway_runtime: bool) -> None:
    """Reject missing, short, or placeholder session secrets on Railway."""
    secret = secret_key.strip()
    if railway_runtime and (
        not secret or secret == "change-me-in-production" or len(secret) < 32
    ):
        raise RuntimeError(
            "SECRET_KEY must be configured in Railway with a unique random value of at least 32 characters "
            "and must not use the development placeholder."
        )


class Settings(BaseSettings):
    app_name: str = "SolenOS"
    debug: bool = False
    database_url: str = "sqlite:///./solenos.db"
    secret_key: str = ""
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    cors_origins: str = "http://localhost:5173,http://localhost:3000,https://solenosai.netlify.app"

    # Local development default only. Railway must set OLLAMA_BASE_URL to the
    # private URL of a separate Ollama service; localhost is a different container.
    ollama_base_url: str = ""
    ollama_model: str = "qwen2.5:1.5b"
    ollama_timeout_seconds: float = 60.0

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def ollama_configured(self) -> bool:
        url = self.ollama_base_url.strip()
        model = self.ollama_model.strip()
        if not url or not model:
            return False

        # Qwen must never fall back to a developer desktop or public hosted endpoint.
        # Only Railway's private service DNS is accepted in every environment.
        return is_railway_private_ollama_url(url) and not is_loopback_ollama_url(url)

    def validate_production_configuration(self) -> None:
        """Reject unsafe storage or session-signing configuration before serving requests.

        Local development may use SQLite and a development-only secret. Production
        (including Railway deployments) must use PostgreSQL and a unique secret.
        """
        railway_runtime = any(os.getenv(name) for name in RAILWAY_RUNTIME_VARIABLES)
        environment = (
            os.getenv("APP_ENV")
            or os.getenv("ENVIRONMENT")
            or os.getenv("RAILWAY_ENVIRONMENT_NAME")
            or os.getenv("RAILWAY_ENVIRONMENT")
            or ""
        ).strip().lower()
        production = railway_runtime or environment in {"production", "prod"}

        if not production:
            return

        database_url = self.database_url.strip().lower()
        if not database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            raise RuntimeError(
                "Production must use managed PostgreSQL for persistent care records; "
                "SQLite is supported only for local development and tests."
            )

        secret = self.secret_key.strip()
        validate_deployment_secret(secret, railway_runtime=True)
        if not secret or secret == "change-me-in-production" or len(secret) < 32:
            raise RuntimeError(
                "SECRET_KEY must be a unique random value of at least 32 characters "
                "before starting SolenOS in production."
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production_configuration()
    return settings
