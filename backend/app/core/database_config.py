"""Deployment-safe database URL handling."""
import os

from sqlalchemy.engine import make_url


def is_railway_runtime(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return any(
        str(env.get(name, "")).strip()
        for name in ("RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME")
    )


def normalize_database_url(url: str) -> str:
    value = (url or "").strip()
    if value.startswith("postgres://"):
        return "postgresql+psycopg2://" + value[len("postgres://"):]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg2://" + value[len("postgresql://"):]
    return value


def configure_database_url(url: str, *, railway_runtime: bool) -> str:
    normalized = normalize_database_url(url)
    if not railway_runtime:
        return normalized
    if not normalized.startswith("postgresql+psycopg2://"):
        raise RuntimeError(
            "Railway requires DATABASE_URL to point to managed PostgreSQL. "
            "SQLite on an application container is not durable production storage."
        )
    parsed = make_url(normalized)
    if parsed.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
        parsed = parsed.update_query_dict({"sslmode": "require"})
    return parsed.render_as_string(hide_password=False)
