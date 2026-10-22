"""Database URL normalization and deployment safety checks.

Railway must use a managed PostgreSQL database. SQLite remains available for
local development only; it is not durable storage on an ephemeral app container.
"""
import os

from sqlalchemy.engine import make_url


def is_railway_runtime(environ=None) -> bool:
    env = os.environ if environ is None else environ
    return any(
        str(env.get(name, "")).strip()
        for name in (
            "RAILWAY_PROJECT_ID",
            "RAILWAY_ENVIRONMENT",
            "RAILWAY_ENVIRONMENT_NAME",
        )
    )


def normalize_database_url(url: str) -> str:
    """Normalize common hosted-Postgres URLs to the installed psycopg2 driver."""
    value = (url or "").strip()
    if value.startswith("postgres://"):
        return "postgresql+psycopg2://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg2://" + value[len("postgresql://") :]
    return value


def configure_database_url(url: str, *, railway_runtime: bool) -> str:
    """Reject ephemeral/unsupported storage in Railway and require TLS for Postgres."""
    normalized = normalize_database_url(url)
    if not railway_runtime:
        return normalized

    if not normalized.startswith("postgresql+psycopg2://"):
        raise RuntimeError(
            "Railway runtime requires DATABASE_URL to point to PostgreSQL. "
            "SQLite is local-development-only and is not durable on Railway."
        )

    parsed = make_url(normalized)
    sslmode = parsed.query.get("sslmode")
    if sslmode not in {"require", "verify-ca", "verify-full"}:
        parsed = parsed.update_query_dict({"sslmode": "require"})
    return parsed.render_as_string(hide_password=False)
