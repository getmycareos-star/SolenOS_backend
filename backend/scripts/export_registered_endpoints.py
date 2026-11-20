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

try:
    from fastapi.routing import iter_route_contexts
except ImportError:
    iter_route_contexts = None

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


def _dependency_names(dependant: Any) -> list[str]:
    names: set[str] = set()

    def visit(dependant: Any) -> None:
        call = getattr(dependant, "call", None)
        if call is not None:
            names.add(
                f"{getattr(call, '__module__', 'unknown')}.{getattr(call, '__qualname__', getattr(call, '__name__', type(call).__name__))}"
            )
        for child in getattr(dependant, "dependencies", []) or []:
            visit(child)

    visit(dependant)
    return sorted(names)


def _registered_route_contexts(application: FastAPI):
    if iter_route_contexts is not None:
        yield from iter_route_contexts(application.routes)
        return
    yield from application.routes


def registered_route_keys(application: FastAPI):
    for context in _registered_route_contexts(application):
        route = getattr(context, "route", context)
        if not isinstance(route, APIRoute):
            continue
        effective_path = getattr(context, "path", route.path)
        effective_methods = getattr(context, "methods", route.methods)
        for method in effective_methods or []:
            yield (method.upper(), effective_path)


def build_inventory(application: FastAPI) -> dict[str, Any]:
    """Return a deterministic inventory of every registered HTTP API route."""
    entries: list[dict[str, Any]] = []
    contexts = []
    for context in _registered_route_contexts(application):
        route = getattr(context, "route", context)
        if isinstance(route, APIRoute):
            contexts.append((context, route))
    registration_counts: dict[tuple[str, str], int] = {}
    for context, route in contexts:
        effective_path = getattr(context, "path", route.path)
        effective_methods = getattr(context, "methods", route.methods)
        for method in effective_methods or []:
            key = (method.upper(), effective_path)
            registration_counts[key] = registration_counts.get(key, 0) + 1

    for context, route in contexts:
        endpoint = getattr(context, "endpoint", route.endpoint)
        dependant = getattr(context, "dependant", None) or route.dependant
        effective_path = getattr(context, "path", route.path)
        effective_methods = getattr(context, "methods", route.methods)
        module = getattr(endpoint, "__module__", "unknown")
        handler = getattr(endpoint, "__qualname__", getattr(endpoint, "__name__", "unknown"))
        for method in sorted(effective_methods or []):
            method = method.upper()
            key = (method, effective_path)
            entries.append(
                {
                    "method": method,
                    "operation_id": route.operation_id,
                    "include_in_schema": route.include_in_schema,
                    "deprecated": bool(route.deprecated),
                    "path": effective_path,
                    "name": route.name,
                    "router_module": module,
                    "handler": handler,
                    "tags": sorted(route.tags or []),
                    "request_model": _model_name(
                        getattr(getattr(route, "body_field", None), "type_", None)
                    ),
                    "request_parameters": {
                        "path": _parameter_fields(dependant.path_params),
                        "query": _parameter_fields(dependant.query_params),
                        "headers": _parameter_fields(dependant.header_params),
                        "cookies": _parameter_fields(dependant.cookie_params),
                        "body": _parameter_fields(dependant.body_params),
                    },
                    "response_model": _model_name(route.response_model),
                    "dependencies": _dependency_names(dependant),
                    "authentication_disposition": (
                        "global_auth_policy_review_required"
                        if effective_path.startswith("/api/")
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
        "source": "app.main:app expanded runtime route contexts",
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
