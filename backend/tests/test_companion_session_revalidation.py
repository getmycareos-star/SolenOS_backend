"""Regression tests for Companion's final bearer-session validation boundary."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.collaboration import _resolve_anonymous_session_id
from app.models.anonymous_session import AnonymousSession


class _Query:
    def __init__(self, result):
        self.result = result
        self.criteria = None

    def filter(self, *criteria):
        self.criteria = criteria
        return self

    def first(self):
        return self.result


class _Database:
    def __init__(self, result):
        self.query_model = None
        self.query_result = _Query(result)

    def query(self, model):
        self.query_model = model
        return self.query_result


def _request(token="valid-token", state_session_id=None):
    return SimpleNamespace(
        headers={"authorization": f"Bearer {token}"},
        state=SimpleNamespace(anonymous_session_id=state_session_id),
    )


def test_companion_session_resolution_always_queries_live_bearer_session():
    session = SimpleNamespace(id="session-1")
    db = _Database(session)
    request = _request()

    resolved = _resolve_anonymous_session_id(
        request, db, "person-1", "caregiver-1"
    )

    assert resolved == "session-1"
    assert request.state.anonymous_session_id == "session-1"
    assert db.query_model is AnonymousSession
    assert db.query_result.criteria


def test_companion_session_resolution_does_not_trust_stale_asgi_session_id():
    db = _Database(SimpleNamespace(id="session-from-current-token"))
    request = _request(state_session_id="session-from-earlier-request")

    with pytest.raises(HTTPException) as raised:
        _resolve_anonymous_session_id(request, db, "person-1", "caregiver-1")

    assert raised.value.status_code == 401
    assert db.query_model is AnonymousSession


def test_companion_session_resolution_rejects_expired_or_revoked_bearer_session():
    db = _Database(None)
    request = _request(state_session_id="cached-session")

    with pytest.raises(HTTPException) as raised:
        _resolve_anonymous_session_id(request, db, "person-1", "caregiver-1")

    assert raised.value.status_code == 401
    assert db.query_model is AnonymousSession


def test_companion_session_resolution_rejects_missing_bearer_token():
    db = _Database(SimpleNamespace(id="session-1"))
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(anonymous_session_id="cached-session"),
    )

    with pytest.raises(HTTPException) as raised:
        _resolve_anonymous_session_id(request, db, "person-1", "caregiver-1")

    assert raised.value.status_code == 401
    assert db.query_model is None
