"""Authorization regressions for the longitudinal evidence API."""
from app import main as main_module
from app.core.security import get_current_principal, AuthenticatedPrincipal
from app.models.identity import Account
from app.core.identity_enums import AccountType


def _principal(person_ids):
    account = Account(
        id="longitudinal-auth-test-account",
        email="longitudinal-auth-test@example.com",
        account_type=AccountType.CAREGIVER,
        is_active=True,
    )
    return AuthenticatedPrincipal(account, person_ids)


def test_longitudinal_evidence_rejects_unauthenticated_requests(client):
    main_module.app.dependency_overrides.pop(get_current_principal, None)
    response = client.get("/api/v1/longitudinal/evidence/timeline/p1")
    assert response.status_code == 401, response.text


def test_longitudinal_evidence_denies_unlinked_person(client):
    main_module.app.dependency_overrides[get_current_principal] = lambda: _principal(["p1"])
    response = client.get("/api/v1/longitudinal/evidence/timeline/unlinked-person")
    assert response.status_code == 403, response.text


def test_longitudinal_evidence_allows_linked_person(client, person):
    main_module.app.dependency_overrides[get_current_principal] = lambda: _principal(["p1"])
    response = client.get("/api/v1/longitudinal/evidence/timeline/p1")
    assert response.status_code == 200, response.text
