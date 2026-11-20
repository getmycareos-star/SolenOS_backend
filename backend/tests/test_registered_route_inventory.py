from scripts.export_registered_routes import build_inventory as build_registered_route_inventory
from scripts.export_endpoint_inventory import build_inventory as build_openapi_endpoint_inventory


def test_runtime_endpoint_inventory_expands_nested_routers_and_matches_product_routes():
    inventory = build_registered_route_inventory()
    routes = {
        (entry["method"], entry["endpoint"])
        for entry in inventory["routes"]
    }

    # A small count would indicate that only the app's directly registered
    # routes were read and the lazy include_router tree was silently omitted.
    assert inventory["route_count"] > 100
    assert ("POST", "/api/v1/sessions/anonymous") in routes
    assert ("POST", "/api/v1/conversation/message") in routes
    assert ("GET", "/api/v1/care-network-state") in routes
    assert ("GET", "/api/v1/care-continuity/context") in routes
    assert not any(path == "/api/v1/community" or path.startswith("/api/v1/community/") for _, path in routes)
    assert all(entry["registered"] for entry in inventory["routes"])

    openapi_inventory = build_openapi_endpoint_inventory()
    assert openapi_inventory["endpoint_count"] > 100
    assert openapi_inventory["registered_handler_match_count"] > 100
    assert openapi_inventory["owner_resolution_required_count"] == 0
    assert openapi_inventory["community_route_count"] == 0
