from app.core.identity_enums import AccountType
from app.core.security import AuthenticatedPrincipal, get_current_principal
from app.main import app
from app.models.identity import Account


def principal(account_type, person_ids):
    account = Account(
        id=f"safety-auth-{account_type.value}",
        email=f"safety-auth-{account_type.value}@example.com",
        account_type=account_type,
        is_active=True,
    )
    return AuthenticatedPrincipal(account, person_ids)


def test_safety_record_read_requires_authentication(client, person):
    response = client.get(f"/api/v1/audit/{person.id}")
    assert response.status_code in (401, 403), response.text


def test_safety_record_read_denies_other_care_context(client, person):
    app.dependency_overrides[get_current_principal] = lambda: principal(
        AccountType.CAREGIVER, ["some-other-person"]
    )
    response = client.get(f"/api/v1/audit/{person.id}")
    assert response.status_code == 403, response.text


def test_safety_record_read_allows_authorized_caregiver(client, person):
    app.dependency_overrides[get_current_principal] = lambda: principal(
        AccountType.CAREGIVER, [person.id]
    )
    response = client.get(f"/api/v1/audit/{person.id}")
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_safety_policy_controls_require_operations_role(client):
    app.dependency_overrides[get_current_principal] = lambda: principal(
        AccountType.CAREGIVER, []
    )
    response = client.get("/api/v1/policy-rules")
    assert response.status_code == 403, response.text


def test_safety_policy_controls_allow_operations_role(client):
    app.dependency_overrides[get_current_principal] = lambda: principal(
        AccountType.SOLENOS_STAFF, []
    )
    response = client.get("/api/v1/policy-rules")
    assert response.status_code == 200, response.text
