"""Shared test isolation for legacy behavior tests after auth became mandatory.

The dedicated test_auth_api.py suite exercises real bearer sessions and access grants.
Other pre-existing route tests focus on domain behavior; they use this explicit test-only
bypass so they don't all need to duplicate the auth integration fixture.
"""
from types import SimpleNamespace
import importlib

import pytest


@pytest.fixture(autouse=True)
def authenticated_domain_test_client(request, monkeypatch):
    if request.module.__name__.endswith(("test_auth_api", "test_capture_api", "test_collaboration_api", "test_daily_capture", "test_capture_candidate_review", "test_anonymous_session")):
        yield
        return

    from app.main import app
    from app.api.authentication import current_account

    test_account = SimpleNamespace(
        id="domain-test-account",
        email="domain-test@example.invalid",
        display_name="Domain Test",
        is_active=True,
        is_staff=True,
    )

    def allow_test_care_record(*args, **kwargs):
        return SimpleNamespace(
            account_id="domain-test-account",
            person_id=kwargs.get("person_id", args[2] if len(args) > 2 else None),
            role="owner",
            permissions=["read", "write", "admin"],
            is_active=True,
        )

    app.dependency_overrides[current_account] = lambda: test_account
    for module_name in (
        "app.core.authz",
        "app.api.v1",
        "app.api.uncertainty",
        "app.api.change",
        "app.api.causal",
        "app.api.caregiver_intelligence",
        "app.api.safety",
        "app.api.actions",
        "app.main",
    ):
        module = importlib.import_module(module_name)
        if hasattr(module, "assert_person_access"):
            monkeypatch.setattr(module, "assert_person_access", allow_test_care_record)
        if hasattr(module, "assert_account_is_caregiver"):
            monkeypatch.setattr(module, "assert_account_is_caregiver", lambda *args, **kwargs: None)
        if module_name == "app.api.caregiver_intelligence" and hasattr(module, "_assert_profile_account"):
            monkeypatch.setattr(module, "_assert_profile_account", lambda *args, **kwargs: None)

    yield
    app.dependency_overrides.pop(current_account, None)
