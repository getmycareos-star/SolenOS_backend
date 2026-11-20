from scripts.export_registered_routes import build_inventory


def test_runtime_endpoint_inventory_expands_nested_routers_and_matches_product_routes():
    inventory = build_inventory()
    routes = {
        (entry["method"], entry["endpoint"])
        for entry in inventory["routes"]
    }

    # A small count would indicate that only the app's directly registered
    # routes were read and the lazy include_router tree was silently omitted.
    assert inventory["route_count"] > 100
    assert inventory["unique_method_path_count"] == inventory["route_count"]
    assert inventory["duplicate_method_paths"] == {}
    assert ("POST", "/api/v1/sessions/anonymous") in routes
    assert ("POST", "/api/v1/conversation/message") in routes
    assert ("GET", "/api/v1/care-network-state") in routes
    assert ("GET", "/api/v1/care-continuity/context") in routes
    assert not any(path == "/api/v1/community" or path.startswith("/api/v1/community/") for _, path in routes)
    assert all(entry["registered"] for entry in inventory["routes"])

    assert any(route["disposition"] == "frontend_product_workflow" for route in inventory["routes"])
    assert any(route["disposition"] == "internal_service_or_operations" for route in inventory["routes"])
    assert all(route["authentication"]["disposition"] for route in inventory["routes"])
    assert all("disposition" in route and "consumer" in route and "persistence" in route for route in inventory["routes"])
    # Unresolved consumer/persistence ownership is emitted explicitly as a review item,
    # never silently omitted or described as verified.
    assert all(route["verification"].startswith("runtime registration") for route in inventory["routes"])
    assert all(route["consumer"] != "REVIEW_REQUIRED: no consumer domain mapping" for route in inventory["routes"])
    assert all(route["persistence"] != "REVIEW_REQUIRED: no persistence domain mapping" for route in inventory["routes"])
    assert all(
        route["status"] == "registered; consumer and persistence domain mapped; end-to-end verification pending"
        for route in inventory["routes"]
    )
    assert all("inspect route dependencies" not in route["authentication"]["disposition"] for route in inventory["routes"])
