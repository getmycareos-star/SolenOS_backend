import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.session_security
def test_document_extraction_requires_a_valid_bearer_session():
    response = TestClient(app).post(
        "/api/v1/documents/extract",
        files={"file": ("care.txt", b"Discharge instructions", "text/plain")},
    )
    assert response.status_code == 401
    assert "session" in response.json()["detail"].lower()
