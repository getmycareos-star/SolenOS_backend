"""Deployment-safe database URL handling."""
import os

from sqlalchemy.engine import make_url

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
    if not normalized.startswith("postgresql+psycopg2://"):
        if railway_runtime:
            raise RuntimeError(
                "Railway requires DATABASE_URL to point to managed PostgreSQL. "
                "SQLite on an application container is not durable production storage."
            )
        return normalized

    parsed = make_url(normalized)
    pooler_host = os.getenv("SUPABASE_POOLER_HOST", _SUPABASE_POOLER_HOST)

    # The Session Pooler always needs a project-qualified username, including
    # pre-deploy contexts where Railway's injected runtime markers may be absent.
    if parsed.host == pooler_host and parsed.username == "postgres":
        parsed = parsed.set(username=f"postgres.{_SUPABASE_PROJECT_REF}")

    if railway_runtime and parsed.host == _SUPABASE_DIRECT_HOST:
        # This project's direct endpoint resolves to IPv6, unreachable from
        # this Railway runtime. Route it through the IPv4 Session Pooler.
        parsed = parsed.set(
            username=f"postgres.{_SUPABASE_PROJECT_REF}" if parsed.username == "postgres" else parsed.username,
            host=pooler_host,
            port=5432,
        )

    if railway_runtime and not parsed.host:
        raise RuntimeError("Railway DATABASE_URL must include a PostgreSQL host.")

    if parsed.query.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
        parsed = parsed.update_query_dict({"sslmode": "require"})
    return parsed.render_as_string(hide_password=False)
