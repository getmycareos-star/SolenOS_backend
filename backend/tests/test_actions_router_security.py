"""The legacy action API must stay unmounted until every operation is session-scoped and audited."""

from app.main import app
from scripts.export_registered_routes import build_inventory


def test_legacy_action_routes_are_not_exposed():
    paths = {
        getattr(route, "path", "")
        for route in app.routes
    }

    assert not any(path.startswith("/api/v1/actions") for path in paths)


def test_existing_reminder_workflow_remains_mounted():
    # Newer FastAPI versions defer included-router expansion in app.routes;
    # OpenAPI is the authoritative resolved route table.
    paths = set(app.openapi()["paths"])

    assert "/api/v1/reminders" in paths
    assert "/api/v1/reminders/{person_id}" in paths
    assert "/api/v1/reminders/{reminder_id}/complete" in paths
    assert "/api/v1/reminders/{reminder_id}/cancel" in paths


def test_registered_action_routes_are_mounted_once_with_unique_operation_ids():
    paths = app.openapi()["paths"]
    expected = {
        ("/api/v1/registered-actions/{person_id}/approve", "post"),
        ("/api/v1/registered-actions/{person_id}", "get"),
        ("/api/v1/registered-actions/{action_id}/history", "get"),
        ("/api/v1/registered-actions/{action_id}/status", "patch"),
    }
    operation_ids = []
    actual = set()
    for path, methods in paths.items():
        for method, operation in methods.items():
            if method.lower() in {"get", "post", "patch", "put", "delete"}:
                operation_ids.append(operation.get("operationId"))
                if path.startswith("/api/v1/registered-actions/"):
                    actual.add((path, method.lower()))
    assert actual == expected
    assert len(operation_ids) == len(set(operation_ids))


def test_runtime_endpoint_inventory_expands_registered_routers_and_cross_checks_openapi():
    inventory = build_inventory()

    assert inventory["route_count"] > 0
    assert inventory["unique_method_path_count"] == inventory["route_count"]
    assert inventory["duplicate_method_paths"] == {}

    for route in inventory["routes"]:
        assert route["registered"] is True
        assert route["router_module"]
        assert route["handler"]
        assert route["authentication"]["disposition"]
        assert route["consumer"]
        assert route["persistence"]
        if route["include_in_schema"]:
            assert route["included_in_openapi"] is True, (
                route["method"], route["endpoint"], route["router_module"], route["handler"]
            )


def test_runtime_endpoint_inventory_preserves_community_suspension():
    inventory = build_inventory()
    assert not any(
        route["endpoint"].lower().startswith("/api/v1/community")
        for route in inventory["routes"]
    )
    assert not any(
        path.lower().startswith("/api/v1/community")
        for path in app.openapi()["paths"]
    )
