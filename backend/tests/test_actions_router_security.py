"""The legacy action API must stay unmounted until every operation is session-scoped and audited."""

from app.main import app


def test_legacy_action_routes_are_not_exposed():
    paths = {
        getattr(route, "path", "")
        for route in app.routes
    }

    assert not any(path.startswith("/api/v1/actions") for path in paths)


def test_existing_reminder_workflow_remains_mounted():
    paths = {
        getattr(route, "path", "")
        for route in app.routes
    }

    assert "/api/v1/reminders" in paths
    assert "/api/v1/reminders/{person_id}" in paths
    assert "/api/v1/reminders/{reminder_id}/complete" in paths
    assert "/api/v1/reminders/{reminder_id}/cancel" in paths
