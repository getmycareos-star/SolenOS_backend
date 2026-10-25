from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)

def test_professional_signup_login_and_private_dashboard():
    email = "professional-auth-test@example.test"
    response = client.post("/api/v1/professional-auth/register", json={
        "email": email, "password": "a-long-test-password", "full_name": "Test Professional",
        "professional_title": "Dementia Care Educator", "credentials": "Test credentials",
        "specialties": "dementia care, caregiver education"})
    assert response.status_code == 201, response.text
    created = response.json()
    token = created["access_token"]
    assert created["session_type"] == "professional"
    assert created["professional"]["status"] == "pending_review"
    assert "password" not in created["professional"]
    assert client.get("/api/v1/professional-auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get("/api/v1/professional-auth/me").status_code == 401
    login = client.post("/api/v1/professional-auth/login", json={"email": email.upper(), "password": "a-long-test-password"})
    assert login.status_code == 200, login.text
    assert login.json()["professional"]["id"] == created["professional"]["id"]
    assert client.post("/api/v1/professional-auth/login", json={"email": email, "password": "wrong-password"}).status_code == 401

def test_professional_cannot_use_caregiver_api_with_professional_token():
    created = client.post("/api/v1/professional-auth/register", json={
        "email": "professional-isolation-test@example.test", "password": "another-long-test-password",
        "full_name": "Isolated Professional", "professional_title": "Social Worker"})
    assert created.status_code == 201, created.text
    token = created.json()["access_token"]
    response = client.get("/api/v1/sessions/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401

def test_professional_logout_and_delete():
    email = "professional-delete-test@example.test"
    created = client.post("/api/v1/professional-auth/register", json={
        "email": email, "password": "delete-me-long-password",
        "full_name": "Delete Test", "professional_title": "Care Consultant"})
    assert created.status_code == 201, created.text
    token = created.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/v1/professional-auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/professional-auth/me", headers=headers).status_code == 401
    second = client.post("/api/v1/professional-auth/login", json={"email": email, "password": "delete-me-long-password"})
    assert second.status_code == 200, second.text
    assert client.delete("/api/v1/professional-auth/account", headers={"Authorization": f"Bearer {second.json()['access_token']}"}).status_code == 200
    assert client.post("/api/v1/professional-auth/login", json={"email": email, "password": "delete-me-long-password"}).status_code == 401
