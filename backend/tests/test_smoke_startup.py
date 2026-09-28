"""Smoke tests that verify the FastAPI app starts up and serves requests
without dependency overrides.

These guard against the class of regression where the app imports fine in
isolation but fails to serve real requests because module-level side effects
(database initialization, missing tables, broken model imports) only surface
at startup or first request time.
"""
from fastapi.testclient import TestClient


def test_health_endpoint():
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["app"] == "SolenOS"


def test_root_endpoint():
    from app.main import app

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "api" in data
    assert "docs" in data
