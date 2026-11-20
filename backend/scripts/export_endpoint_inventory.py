"""Export the actual FastAPI OpenAPI route table with its owning handlers.

Run from the backend directory:
    python scripts/export_endpoint_inventory.py --output reports/registered-endpoint-inventory.json

The inventory is generated from app.openapi(), not source-file grep. It is a
runtime registration inventory; consumer and persistence ownership remain
explicit review fields rather than guessed from a matching URL.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute

from app.main import PUBLIC_V1_ENDPOINTS, app


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _schema_name(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        return value["$ref"].rsplit("/", 1)[-1]
    if "type" in value:
        result = {"type": value["type"]}
        if "items" in value:
            result["items"] = _schema_name(value["items"])
        if "enum" in value:
            result["enum"] = value["enum"]
        return result
    return value


def _auth_disposition(path: str, method: str) -> str:
    if path == "/api/v1/assistant/evaluate-production" and method.lower() == "post":
        return "github-actions-token-and-live-main-workflow-verification"
    if (
        path == "/api/v1/privacy/requests/reviewer/pending"
        or re.fullmatch(
            r"/api/v1/privacy/requests/[^/]+/(?:review|fulfil-export|fulfil-correction|fulfil-deletion)",
            path,
        )
    ):
        return "privacy-reviewer-credential-required"
    if (path, method.upper()) in PUBLIC_V1_ENDPOINTS:
        return "explicit-public-exception"
    if path == "/api/v1/sessions/anonymous" and method.lower() == "post":
        return "anonymous-session-bootstrap"
    if path == "/api/v1/share-intake" and method.lower() == "post":
        return "share-token-bootstrap"
    if path.startswith("/api/v1/share-intake/") and method.lower() == "get":
        return "share-token-read"
    if path.startswith("/api/v1/privacy/requests/reviewer/") or "/reviewer/" in path:
        return "reviewer-credential-required"
    if path.startswith("/api/v1/") or path in {"/api/situation", "/api/lcr/orientation"}:
        return "server-issued-bearer-session-required"
    return "outside-v1-session-middleware; inspect route dependencies"


def build_inventory() -> dict[str, Any]:
    openapi = app.openapi()
    route_index: dict[tuple[str, str], list[APIRoute]] = defaultdict(list)
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method.lower() in HTTP_METHODS:
                route_index[(route.path, method.lower())].append(route)

    endpoints: list[dict[str, Any]] = []
    for path, operations in sorted(openapi.get("paths", {}).items()):
        for method, operation in sorted(operations.items()):
            method_lower = method.lower()
            if method_lower not in HTTP_METHODS:
                continue
            matches = route_index.get((path, method_lower), [])
            owners = [
                {
                    "module": route.endpoint.__module__,
                    "handler": route.endpoint.__name__,
                    "route_name": route.name,
                }
                for route in matches
            ]
            owner_resolution = (
                "resolved-from-registered-APIRoute"
                if len(matches) == 1
                else "multiple-registered-APIRoute-matches"
                if len(matches) > 1
                else "openapi-operation-only; FastAPI may defer included-router expansion"
            )
            if not owners:
                owners = [{
                    "module": None,
                    "handler": operation.get("operationId"),
                    "router_tags": operation.get("tags", []),
                }]
            parameters = []
            for parameter in operation.get("parameters", []):
                parameters.append({
                    "name": parameter.get("name"),
                    "in": parameter.get("in"),
                    "required": parameter.get("required", False),
                    "schema": _schema_name(parameter.get("schema", {})),
                })
            request_body = operation.get("requestBody", {})
            request_content = request_body.get("content", {})
            request_schemas = {
                content_type: _schema_name(spec.get("schema", {}))
                for content_type, spec in request_content.items()
            }
            response_schemas = {}
            for status, response in operation.get("responses", {}).items():
                response_schemas[status] = {
                    content_type: _schema_name(spec.get("schema", {}))
                    for content_type, spec in response.get("content", {}).items()
                }
            endpoints.append({
                "method": method.upper(),
                "path": path,
                "operation_id": operation.get("operationId"),
                "summary": operation.get("summary"),
                "tags": operation.get("tags", []),
                "owners": owners,
                "owner_resolution": owner_resolution,
                "authentication": _auth_disposition(path, method),
                "parameters": parameters,
                "request_body_required": request_body.get("required", False),
                "request_schemas": request_schemas,
                "response_schemas": response_schemas,
                "consumer": "REVIEW_REQUIRED: name the frontend workflow or internal service",
                "persistence": "REVIEW_REQUIRED: identify tables and transaction boundary",
                "integration_status": (
                    "registered-handler-match"
                    if len(matches) == 1
                    else "owner-resolution-required"
                    if not matches
                    else "duplicate-handler-match"
                ),
                "verification": "REVIEW_REQUIRED: attach contract, persistence and authorization test evidence",
            })

    community_routes = [
        endpoint for endpoint in endpoints
        if endpoint["path"].lower().startswith("/api/v1/community")
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "FastAPI app.openapi() and registered APIRoute handlers",
        "application": app.title,
        "version": app.version,
        "endpoint_count": len(endpoints),
        "registered_handler_match_count": sum(
            endpoint["integration_status"] == "registered-handler-match"
            for endpoint in endpoints
        ),
        "owner_resolution_required_count": sum(
            endpoint["integration_status"] == "owner-resolution-required"
            for endpoint in endpoints
        ),
        "duplicate_handler_match_count": sum(
            endpoint["integration_status"] == "duplicate-handler-match"
            for endpoint in endpoints
        ),
        "community_route_count": len(community_routes),
        "endpoints": endpoints,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports/registered-endpoint-inventory.json")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    inventory = build_inventory()
    output.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "REGISTERED_ENDPOINT_INVENTORY "
        f"count={inventory['endpoint_count']} "
        f"matched={inventory['registered_handler_match_count']} "
        f"owner_review={inventory['owner_resolution_required_count']} "
        f"duplicates={inventory['duplicate_handler_match_count']} "
        f"community={inventory['community_route_count']} "
        f"output={output}"
    )
    if inventory["duplicate_handler_match_count"]:
        return 1
    if inventory["community_route_count"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
