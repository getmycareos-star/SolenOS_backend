from app.models.identity import Person
from app.models.caregiver import Caregiver


def test_client_only(client, db):
    """Exercise a real graph endpoint with an isolated DB and scoped principal."""
    from app.main import app
    from app.core.security import get_current_active_user, AuthenticatedPrincipal
    from app.models.identity import Account
    from app.core.identity_enums import AccountType

    person_id = "test-person"
    person = Person(id=person_id, name="Test Person")
    caregiver = Caregiver(
        id="test-caregiver",
        name="Test Caregiver",
        email="test@example.com",
        relationship="daughter",
        person_id=person_id,
    )
    db.add_all([person, caregiver])
    db.commit()

    account = Account(
        id="client-only-test-account",
        is_active=True,
        account_type=AccountType.CAREGIVER,
    )
    principal = AuthenticatedPrincipal(account, [person_id])
    app.dependency_overrides[get_current_active_user] = lambda: principal
    try:
        response = client.get(f"/api/v1/graph/{person_id}/snapshot")
        assert response.status_code == 200, response.text
        data = response.json()
        assert isinstance(data.get("nodes"), list)
        assert isinstance(data.get("edges"), list)
    finally:
        app.dependency_overrides.pop(get_current_active_user, None)
