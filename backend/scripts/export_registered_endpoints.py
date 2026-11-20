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


def _parameter_fields(fields: list[Any]) -> list[dict[str, Any]]:
    output = []
    for field in fields or []:
        annotation = getattr(field, "annotation", None) or getattr(field, "outer_type_", None) or getattr(field, "type_", None)
        required_value = getattr(field, "is_required", None)
        required = required_value() if callable(required_value) else bool(getattr(field, "required", False))
        default = getattr(field, "default", None)
        if default is not None and not isinstance(default, (str, int, float, bool)):
            default = None
        output.append({
            "name": getattr(field, "name", None),
            "alias": getattr(field, "alias", None),
            "type": str(annotation) if annotation is not None else None,
            "required": required,
            "default": default,
        })
    return output


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
    registered_routes = [route for route in application.routes if isinstance(route, APIRoute)]
    registration_counts: dict[tuple[str, str], int] = {}
    for route in registered_routes:
        for method in route.methods or []:
            key = (method, route.path)
            registration_counts[key] = registration_counts.get(key, 0) + 1

    for route in registered_routes:
        endpoint = route.endpoint
        module = getattr(endpoint, "__module__", "unknown")
        handler = getattr(endpoint, "__qualname__", getattr(endpoint, "__name__", "unknown"))
        for method in sorted(route.methods or []):
            key = (method, route.path)
            entries.append(
                {
                    "method": method,
                    "operation_id": route.operation_id,
                    "include_in_schema": route.include_in_schema,
                    "deprecated": bool(route.deprecated),
                    "path": route.path,
                    "name": route.name,
                    "router_module": module,
                    "handler": handler,
                    "tags": sorted(route.tags or []),
                    "request_model": _model_name(
                        getattr(getattr(route, "body_field", None), "type_", None)
                    ),
                    "request_parameters": {
                        "path": _parameter_fields(route.dependant.path_params),
                        "query": _parameter_fields(route.dependant.query_params),
                        "headers": _parameter_fields(route.dependant.header_params),
                        "cookies": _parameter_fields(route.dependant.cookie_params),
                        "body": _parameter_fields(route.dependant.body_params),
                    },
                    "response_model": _model_name(route.response_model),
                    "dependencies": _dependency_names(route),
                    "authentication_disposition": (
                        "global_auth_policy_review_required"
                        if route.path.startswith("/api/")
                        else "public_or_non_api_review_required"
                    ),
                    "consumer_disposition": "unmapped_review_required",
                    "persistence_disposition": "unmapped_review_required",
                    "verification_disposition": "not_yet_assessed",
                    "duplicate_registration": registration_counts[key] > 1,
                    "duplicate_registration_count": registration_counts[key],
                }
            )
    entries.sort(key=lambda item: (item["path"], item["method"], item["router_module"], item["handler"]))
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
