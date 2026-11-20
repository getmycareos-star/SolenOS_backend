from app.main import app
from scripts.export_registered_endpoints import build_inventory


def test_runtime_endpoint_inventory_covers_every_registered_api_route():
    inventory = build_inventory(app)
    registered = {
        (method, route.path)
        for route in app.routes
        if hasattr(route, "methods") and hasattr(route, "path")
        for method in (route.methods or [])
        if hasattr(route, "dependant")
    }
    inventoried = {
        (item["method"], item["path"])
        for item in inventory["routes"]
    }

    assert inventory["source"] == "app.main:app runtime route registry"
    assert inventory["route_count"] == len(inventory["routes"])
    assert inventoried == registered
    assert len(inventoried) == len(inventory["routes"])
    assert all(item["router_module"] and item["handler"] for item in inventory["routes"])
    assert all(item["consumer_disposition"] == "unmapped_review_required" for item in inventory["routes"])
