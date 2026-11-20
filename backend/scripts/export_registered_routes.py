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

from app.main import app


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

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or []):
            method = method.upper()
            operation = paths.get(route.path, {}).get(method.lower())
            key = f"{method} {route.path}"
            duplicates[key].append(
                f"{route.endpoint.__module__}.{route.endpoint.__qualname__}"
            )
            entries.append({
                "endpoint": route.path,
                "method": method,
                "router_module": route.endpoint.__module__,
                "handler": route.endpoint.__qualname__,
                "route_name": route.name,
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
        "source": "runtime app.routes and app.openapi()",
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
