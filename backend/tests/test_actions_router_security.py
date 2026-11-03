"""The legacy action API must stay unmounted until every operation is session-scoped and audited."""

from app.main import app


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
