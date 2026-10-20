import pytest
from app.models.identity import Person, Account
from app.models.caregiver import Caregiver
from app.core.security import get_current_active_user, AuthenticatedPrincipal
from app.core.identity_enums import AccountType


def test_client_only(client, db):
    """Exercise a real graph endpoint through the shared isolated test client."""
    person_id = "test-person"
    caregiver_id = "test-caregiver"

    person = Person(id=person_id, name="Test Person")
    caregiver = Caregiver(
        id=caregiver_id,
        name="Test Caregiver",
        email="test@example.com",
        relationship="daughter",
        person_id=person_id,
    )
    db.add_all([person, caregiver])
    db.commit()

    account = Account(
        id="test-client-only-account",
        is_active=True,
        account_type=AccountType.CAREGIVER,
    )
    principal = AuthenticatedPrincipal(account, [person_id])
    from app.main import app
    app.dependency_overrides[get_current_active_user] = lambda: principal

    response = client.get(f"/api/v1/graph/{person_id}/snapshot")
    assert response.status_code == 200
    assert response.json() is not None
