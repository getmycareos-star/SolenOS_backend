import os
from functools import lru_cache

from pydantic_settings import BaseSettings


RAILWAY_RUNTIME_VARIABLES = (
    "RAILWAY_PROJECT_ID",
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_ENVIRONMENT_NAME",
)


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
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = ""

    class Config:
        env_file = ".env"

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
