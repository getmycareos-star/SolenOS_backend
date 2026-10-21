import os
from functools import lru_cache

from pydantic_settings import BaseSettings


RAILWAY_RUNTIME_VARIABLES = (
    "RAILWAY_PROJECT_ID",
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_ENVIRONMENT_NAME",
)


class Settings(BaseSettings):
    app_name: str = "SolenOS"
    debug: bool = False
    database_url: str = "sqlite:///./solenos.db"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    cors_origins: str = "http://localhost:5173,http://localhost:3000,https://solenosai.netlify.app"

    class Config:
        env_file = ".env"


def validate_deployment_secret(secret_key: str, *, railway_runtime: bool) -> None:
    """Fail closed rather than sign production sessions with the committed placeholder."""
    if railway_runtime and secret_key == "change-me-in-production":
        raise RuntimeError(
            "SECRET_KEY must be configured in Railway before starting SolenOS; "
            "the development placeholder cannot sign production sessions."
        )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    railway_runtime = any(os.getenv(name) for name in RAILWAY_RUNTIME_VARIABLES)
    validate_deployment_secret(settings.secret_key, railway_runtime=railway_runtime)
    return settings
