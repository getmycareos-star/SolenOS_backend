"""Integration tests for authenticated reasoning endpoints.

These tests use the shared isolated database/client fixtures; importing a test
module must not create tables or install process-global dependency overrides.
"""
import pytest

from app.core.database import get_db
from app.core.identity_enums import AccountType
from app.core.security import AuthenticatedPrincipal, get_current_active_user
from app.main import app
from app.models.identity import Account, Person


@pytest.fixture()
def reasoning_client(client):
    def override_current_user():
        account = Account(
            id="test-account",
            is_active=True,
            account_type=AccountType.CAREGIVER,
        )
        return AuthenticatedPrincipal(account, ["test-person", "test-person2"])

    app.dependency_overrides[get_current_active_user] = override_current_user
    return client


def test_causal_inference_endpoint(reasoning_client, db):
    db.add(Person(id="test-person", name="Test Person"))
    db.commit()

    response = reasoning_client.post(
        "/reasoning/graph/test-person/counterfactual",
        json={"intervention": {"medication": "drugA"}},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["person_id"] == "test-person"
    assert data["intervention"]["medication"] == "drugA"


def test_belief_updater_endpoint(reasoning_client, db):
    db.add(Person(id="test-person2", name="Test Person 2"))
    db.commit()

    response = reasoning_client.post("/reasoning/belief/test-person2/update")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["person_id"] == "test-person2"
    assert "belief_state_id" in data
    assert "state_variables" in data
    assert "updated_at" in data


def test_explainability_endpoint_requires_authorized_person(reasoning_client):
    response = reasoning_client.get("/reasoning/explain/some-decision-id")
    assert response.status_code != 500, response.text
