"""Integration tests for authenticated reasoning endpoints."""
import pytest

from app.core.identity_enums import AccountType
from app.core.security import AuthenticatedPrincipal, get_current_active_user
from app.main import app
from app.models.identity import Account, Person

PERSON_ID_1 = "11111111-1111-4111-8111-111111111111"
PERSON_ID_2 = "22222222-2222-4222-8222-222222222222"


@pytest.fixture()
def reasoning_client(client):
    def override_current_user():
        account = Account(
            id="test-account",
            is_active=True,
            account_type=AccountType.CAREGIVER,
        )
        return AuthenticatedPrincipal(account, [PERSON_ID_1, PERSON_ID_2])

    app.dependency_overrides[get_current_active_user] = override_current_user
    return client


def test_causal_inference_endpoint(reasoning_client, db):
    db.add(Person(id=PERSON_ID_1, name="Test Person"))
    db.commit()

    response = reasoning_client.post(
        f"/reasoning/graph/{PERSON_ID_1}/counterfactual",
        json={"intervention": {"medication": "drugA"}},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["person_id"] == PERSON_ID_1
    assert data["intervention"]["medication"] == "drugA"


def test_belief_updater_endpoint(reasoning_client, db):
    db.add(Person(id=PERSON_ID_2, name="Test Person 2"))
    db.commit()

    response = reasoning_client.post(f"/reasoning/belief/{PERSON_ID_2}/update")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["person_id"] == PERSON_ID_2
    assert "belief_state_id" in data
    assert "state_variables" in data
    assert "updated_at" in data


def test_explainability_does_not_disclose_unknown_decisions(reasoning_client):
    response = reasoning_client.get(
        "/reasoning/explain/33333333-3333-4333-8333-333333333333"
    )
    assert response.status_code == 404, response.text
