"""Deployment-safe database URL handling."""
import os

from sqlalchemy.engine import make_url

# Railway's outbound network cannot reliably reach this project's direct IPv6-only
# Supabase database endpoint. Use the project's IPv4-capable Session Pooler while
# preserving the password already stored in DATABASE_URL.
_SUPABASE_PROJECT_REF = "qyialgkfbwralolryhjx"
_SUPABASE_DIRECT_HOST = f"db.{_SUPABASE_PROJECT_REF}.supabase.co"
_SUPABASE_POOLER_HOST = "aws-0-us-west-1.pooler.supabase.com"


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
    if parsed.host == _SUPABASE_DIRECT_HOST:
        # Session Pooler requires a project-qualified username. Updating the
        # parsed URL preserves correctly URL-encoded passwords and query options.
        if parsed.username == "postgres":
            parsed = parsed.set(username=f"postgres.{_SUPABASE_PROJECT_REF}")
        parsed = parsed.set(host=os.getenv("SUPABASE_POOLER_HOST", _SUPABASE_POOLER_HOST), port=5432)

    if parsed.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
        parsed = parsed.update_query_dict({"sslmode": "require"})
    return parsed.render_as_string(hide_password=False)
