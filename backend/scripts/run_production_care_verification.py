#!/usr/bin/env python3
"""Read-only production health verification.

Production verification must never create identities, sessions, evidence, files,
care records, or other synthetic data. All write/persistence/isolation tests run
against disposable PostgreSQL in CI via postgres_anonymous_e2e.py.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = os.getenv(
    "SOLENOS_PRODUCTION_API_BASE_URL",
    "https://solenosbackend-production.up.railway.app",
).strip().rstrip("/")


def get_json(path: str, timeout: int = 15) -> tuple[int, dict]:
    """Issue GET only. Fail closed if this script is ever changed to request a write."""
    method = "GET"
    if method != "GET":
        raise RuntimeError("Production verification is read-only; non-GET requests are forbidden.")
    request = Request(
        BASE + path,
        headers={"Accept": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            if not isinstance(payload, dict):
                raise RuntimeError(f"{path} returned a non-object JSON response.")
            return response.status, payload
    except HTTPError as exc:
        raw = exc.read(4000).decode("utf-8", errors="replace")
        raise RuntimeError(f"{path} returned HTTP {exc.code}: {raw}") from exc
    except (URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"{path} could not be verified: {type(exc).__name__}: {exc}") from exc


def main() -> int:
    if not BASE.startswith("https://"):
        raise RuntimeError("Production API URL must use HTTPS.")

    results = {}
    for path in ("/health", "/ready"):
        status, payload = get_json(path)
        if status != 200:
            raise RuntimeError(f"{path} expected HTTP 200, received {status}.")
        results[path] = {
            "http_status": status,
            "status": payload.get("status"),
            "migration_state": payload.get("migration_state"),
            "continuity_schema": payload.get("continuity_schema"),
        }
        if path == "/ready":
            if payload.get("migration_state") not in (None, "current"):
                raise RuntimeError(f"Production migration state is not current: {payload.get('migration_state')!r}")
            if payload.get("continuity_schema") not in (None, "verified"):
                raise RuntimeError(f"Production continuity schema is not verified: {payload.get('continuity_schema')!r}")

    print(json.dumps({
        "production_verification_mode": "read_only",
        "production_write_requests": 0,
        "synthetic_people_created": 0,
        "synthetic_sessions_created": 0,
        "synthetic_evidence_created": 0,
        "checks": results,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PRODUCTION_READ_ONLY_VERIFICATION_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
