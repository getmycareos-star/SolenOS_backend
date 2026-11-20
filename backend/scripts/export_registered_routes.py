#!/usr/bin/env python3
"""Export the routes actually registered on the FastAPI application.

Run from backend/ after installing the backend package:
    python scripts/export_registered_routes.py --output ../artifacts/registered-routes.json

This is a runtime inventory, not a source-code grep. The generated file deliberately
marks consumer, persistence and disposition as unclassified until each route has
been reviewed against its real service and workflow.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute

try:
    # FastAPI >=0.137 preserves included routers as lazy route-tree nodes.
    # iter_route_contexts expands nested includes and returns effective prefixed paths.
    from fastapi.routing import iter_route_contexts
except ImportError:  # FastAPI <0.137 exposes a flattened app.routes list.
    iter_route_contexts = None

from app.main import app


def _registered_route_contexts():
    if iter_route_contexts is not None:
        yield from iter_route_contexts(app.routes)
        return
    yield from app.routes


def _schema_for_operation(operation: dict[str, Any]) -> dict[str, Any]:
    parameters = []
    for parameter in operation.get("parameters", []):
        parameters.append({
            "name": parameter.get("name"),
            "in": parameter.get("in"),
            "required": bool(parameter.get("required")),
            "schema": parameter.get("schema", {}),
        })
    request_body = operation.get("requestBody", {})
    request_content = request_body.get("content", {})
    request_schema = {
        media_type: details.get("schema", {})
        for media_type, details in request_content.items()
    }
    responses = {}
    for status, response in operation.get("responses", {}).items():
        responses[status] = {
            media_type: details.get("schema", {})
            for media_type, details in response.get("content", {}).items()
        }
    return {
        "operation_id": operation.get("operationId"),
        "summary": operation.get("summary"),
        "parameters": parameters,
        "request_body_required": bool(request_body.get("required")),
        "request_body": request_schema,
        "responses": responses,
        "openapi_security": operation.get("security", []),
    }


def build_inventory() -> dict[str, Any]:
    openapi = app.openapi()
    paths = openapi.get("paths", {})
    entries = []
    duplicates: dict[str, list[str]] = defaultdict(list)

    for route_context in _registered_route_contexts():
        route = getattr(route_context, "route", route_context)
        if not isinstance(route, APIRoute):
            continue
        # RouteContext.path/methods/endpoint include every nested include prefix
        # and dependency context; route.path alone is only the leaf declaration.
        effective_path = getattr(route_context, "path", route.path)
        effective_methods = getattr(route_context, "methods", route.methods)
        effective_endpoint = getattr(route_context, "endpoint", route.endpoint)
        for method in sorted(effective_methods or []):
            method = method.upper()
            operation = paths.get(effective_path, {}).get(method.lower())
            key = f"{method} {effective_path}"
            duplicates[key].append(
                f"{effective_endpoint.__module__}.{effective_endpoint.__qualname__}"
            )
            entries.append({
                "endpoint": effective_path,
                "method": method,
                "router_module": effective_endpoint.__module__,
                "handler": effective_endpoint.__qualname__,
                "route_name": getattr(route_context, "name", route.name),
                "registered": True,
                "included_in_openapi": operation is not None,
                "contract": _schema_for_operation(operation) if operation else None,
                "authentication": {
                    "openapi_security": (operation or {}).get("security", []),
                    "runtime_middleware_and_dependencies": "requires_review",
                },
                "persistence": "requires_service_and_model_trace",
                "consumer": "unclassified",
                "disposition": "unclassified",
                "verification": "not_verified_by_inventory_generation",
            })

    entries.sort(key=lambda item: (item["endpoint"], item["method"], item["handler"]))
    duplicate_routes = {
        key: handlers for key, handlers in duplicates.items() if len(handlers) > 1
    }
    return {
        "title": "SolenOS registered FastAPI route inventory",
        "source": (
            "runtime app.routes expanded with fastapi.routing.iter_route_contexts "
            "when available, cross-checked against app.openapi()"
        ),
        "route_tree_expansion": (
            "iter_route_contexts" if iter_route_contexts is not None else "legacy_flat_app_routes"
        ),
        "application": app.title,
        "version": app.version,
        "route_count": len(entries),
        "unique_method_path_count": len(duplicates),
        "duplicate_method_paths": duplicate_routes,
        "classification_policy": (
            "Unclassified entries are explicit work items; this export does not "
            "claim a frontend/internal consumer, persistence behavior, authorization "
            "or verification without source and runtime evidence."
        ),
        "routes": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    inventory = build_inventory()
    output.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"Exported {inventory['route_count']} registered route-method entries "
        f"({inventory['unique_method_path_count']} unique method/path pairs) "
        f"to {output}"
    )
    if inventory["duplicate_method_paths"]:
        print("Duplicate method/path registrations:")
        for key, handlers in inventory["duplicate_method_paths"].items():
            print(f"  {key}: {', '.join(handlers)}")


if __name__ == "__main__":
    main()
