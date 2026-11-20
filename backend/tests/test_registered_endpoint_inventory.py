"""The generated inventory must enumerate the actual registered API surface."""
from scripts.export_endpoint_inventory import build_inventory


def test_every_openapi_operation_has_exactly_one_registered_fastapi_handler():
    inventory = build_inventory()
    assert inventory["endpoint_count"] > 0
    assert inventory["duplicate_handler_match_count"] == 0
    assert all(endpoint["operation_id"] for endpoint in inventory["endpoints"])
    assert all(endpoint["owners"] for endpoint in inventory["endpoints"])


def test_all_v1_care_routes_have_an_explicit_authentication_disposition():
    inventory = build_inventory()
    for endpoint in inventory["endpoints"]:
        if endpoint["path"].startswith("/api/v1/"):
            assert endpoint["authentication"] != "outside-v1-session-middleware; inspect route dependencies", (
                endpoint["method"], endpoint["path"]
            )


def test_community_remains_absent_from_the_registered_api_surface():
    inventory = build_inventory()
    assert inventory["community_route_count"] == 0
    assert not any(
        endpoint["path"].lower().startswith("/api/v1/community")
        for endpoint in inventory["endpoints"]
    )


def test_every_route_has_a_reviewable_consumer_and_persistence_field():
    inventory = build_inventory()
    for endpoint in inventory["endpoints"]:
        assert endpoint["consumer"]
        assert endpoint["persistence"]
        assert endpoint["verification"]
