from collections import Counter

from app.main import app
from scripts.export_registered_endpoints import build_inventory


def test_runtime_endpoint_inventory_covers_every_registered_api_route():
    inventory = build_inventory(app)
    registered = Counter(
        (method, route.path)
        for route in app.routes
        if hasattr(route, "methods") and hasattr(route, "path") and hasattr(route, "dependant")
        for method in (route.methods or [])
    )
    inventoried = Counter(
        (item["method"], item["path"])
        for item in inventory["routes"]
    )

    assert inventory["source"] == "app.main:app runtime route registry"
    assert inventory["route_count"] == len(inventory["routes"])
    assert inventoried == registered
    assert sum(inventoried.values()) == len(inventory["routes"])
    assert all(
        item["duplicate_registration"] is (registered[(item["method"], item["path"])] > 1)
        for item in inventory["routes"]
    )
    assert all("path" in item["request_parameters"] and "body" in item["request_parameters"] for item in inventory["routes"])
    assert all(item["router_module"] and item["handler"] for item in inventory["routes"])
    assert all(item["consumer_disposition"] == "unmapped_review_required" for item in inventory["routes"])
