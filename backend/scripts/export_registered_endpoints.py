"""Export the exact routes registered by the running FastAPI application.

This is intentionally runtime-based: source files can contain routers that are
never included by app.main, while nested routers and conditional registrations
may not be visible to a filename-only scan.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.main import app


def _model_name(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "__name__", str(value))


def _dependency_names(route: APIRoute) -> list[str]:
    names: set[str] = set()

    def visit(dependant: Any) -> None:
        call = getattr(dependant, "call", None)
        if call is not None:
            names.add(
                f"{getattr(call, '__module__', 'unknown')}.{getattr(call, '__qualname__', getattr(call, '__name__', type(call).__name__))}"
            )
        for child in getattr(dependant, "dependencies", []) or []:
            visit(child)

    visit(route.dependant)
    return sorted(names)


def build_inventory(application: FastAPI) -> dict[str, Any]:
    """Return a deterministic inventory of every registered HTTP API route."""
    entries: list[dict[str, Any]] = []
    for route in application.routes:
        if not isinstance(route, APIRoute):
            continue
        endpoint = route.endpoint
        module = getattr(endpoint, "__module__", "unknown")
        handler = getattr(endpoint, "__qualname__", getattr(endpoint, "__name__", "unknown"))
        for method in sorted(route.methods or []):
            entries.append(
                {
                    "method": method,
                    "path": route.path,
                    "name": route.name,
                    "router_module": module,
                    "handler": handler,
                    "tags": sorted(route.tags or []),
                    "request_model": _model_name(
                        getattr(getattr(route, "body_field", None), "type_", None)
                    ),
                    "response_model": _model_name(route.response_model),
                    "dependencies": _dependency_names(route),
                    "authentication_disposition": (
                        "session_scope_middleware_review_required"
                        if route.path.startswith("/api/")
                        else "public_or_non_api_review_required"
                    ),
                    "consumer_disposition": "unmapped_review_required",
                    "persistence_disposition": "unmapped_review_required",
                    "verification_disposition": "not_yet_assessed",
                }
            )
    entries.sort(key=lambda item: (item["path"], item["method"], item["router_module"], item["handler"]))
    keys = [(item["method"], item["path"]) for item in entries]
    if len(keys) != len(set(keys)):
        duplicates = sorted(key for key in set(keys) if keys.count(key) > 1)
        raise RuntimeError(f"Duplicate registered method/path pairs: {duplicates}")
    return {
        "format_version": 1,
        "source": "app.main:app runtime route registry",
        "route_count": len(entries),
        "routes": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write JSON to this path instead of stdout.")
    args = parser.parse_args()
    inventory = build_inventory(app)
    rendered = json.dumps(inventory, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Exported {inventory['route_count']} registered routes to {args.output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
