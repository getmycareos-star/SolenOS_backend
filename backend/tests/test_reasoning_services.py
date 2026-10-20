import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.models.identity import Person, Account
from app.core.identity_enums import AccountType
from app.core.security import AuthenticatedPrincipal, get_current_active_user
from unittest.mock import patch

# Create a shared in-memory database
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        yield SessionLocal()
    finally:
        SessionLocal().close()

def override_get_current_active_user():
    mock_account = Account(id="test-account", is_active=True, account_type=AccountType.CAREGIVER)
    class MockPrincipal(AuthenticatedPrincipal):
        def has_access_to_person(self, person_id: str) -> bool:
            return True
    return MockPrincipal(mock_account, [])

app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_active_user] = override_get_current_active_user

client = TestClient(app)

def test_causal_inference_endpoint():
    # Create a person
    db = SessionLocal()
    person = Person(id="test-person", name="Test Person")
    db.add(person)
    db.commit()
    db.close()

    # Call the counterfactual endpoint
    response = client.post(
        "/reasoning/graph/test-person/counterfactual",
        json={"intervention": {"medication": "drugA"}},
    )
    assert response.status_code == 200
    data = response.json()
    assert "person_id" in data
    assert data["person_id"] == "test-person"
    assert "intervention" in data
    assert data["intervention"]["medication"] == "drugA"

def test_belief_updater_endpoint():
    db = SessionLocal()
    person = Person(id="test-person2", name="Test Person 2")
    db.add(person)
    db.commit()
    db.close()

    response = client.post("/reasoning/belief/test-person2/update")
    assert response.status_code == 200
    data = response.json()
    assert "belief_state_id" in data
    assert data["person_id"] == "test-person2"
    assert "state_variables" in data
    assert "updated_at" in data

def test_explainability_endpoint():
    response = client.get("/reasoning/explain/some-decision-id")
    # We expect 200 because we don't enforce authorization in the endpoint for simplicity
    assert response.status_code == 200
    data = response.json()
    assert "decision_id" in data
    assert "provenance" in data
    assert "uncertainty" in data
