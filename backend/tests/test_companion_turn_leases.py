"""Unit coverage for the PostgreSQL Companion turn lease protocol."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.collaboration import (
    _acquire_companion_turn_lease,
    _release_companion_turn_leases,
)


class _Result:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _Postgres:
    def __init__(self, outcomes):
        self.dialect = SimpleNamespace(name="postgresql")
        self.outcomes = list(outcomes)
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        return _Result(outcome)

    def get_bind(self):
        return SimpleNamespace(dialect=self.dialect)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _request():
    return SimpleNamespace(state=SimpleNamespace())


class _NonPostgres(_Postgres):
    def __init__(self):
        super().__init__([])
        self.dialect = SimpleNamespace(name="sqlite")


def test_turn_lease_requires_postgresql_and_fails_closed():
    db = _NonPostgres()
    request = _request()

    with pytest.raises(HTTPException) as raised:
        asyncio.run(_acquire_companion_turn_lease(db, request, "conversation:key"))

    assert raised.value.status_code == 503
    assert "requires the PostgreSQL database" in raised.value.detail
    assert db.statements == []
    assert db.commits == 0


def test_turn_lease_acquisition_persists_owner_and_lock_key():
    db = _Postgres([("conversation:key",)])
    request = _request()

    asyncio.run(_acquire_companion_turn_lease(db, request, "conversation:key"))

    assert request.state.companion_turn_leases == ["conversation:key"]
    assert request.state.companion_turn_lease_owner
    assert db.commits == 1
    assert "ON CONFLICT (lock_key) DO UPDATE" in db.statements[0][0]
    assert "WHERE companion_turn_leases.expires_at <= now()" in db.statements[0][0]


def test_turn_lease_conflict_retries_then_returns_conflict(monkeypatch):
    db = _Postgres([None, None, None])
    request = _request()
    ticks = iter([0.0, 2.0, 4.0, 6.0])
    monkeypatch.setattr("app.api.collaboration._time.monotonic", lambda: next(ticks))

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("app.api.collaboration.asyncio.sleep", no_wait)
    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            _acquire_companion_turn_lease(
                db, request, "conversation:key", wait_timeout_seconds=1.0
            )
        )

    assert raised.value.status_code == 409
    assert db.rollbacks >= 1
    assert not getattr(request.state, "companion_turn_leases", [])


def test_turn_lease_database_failure_fails_closed():
    db = _Postgres([RuntimeError("database unavailable")])
    request = _request()

    with pytest.raises(HTTPException) as raised:
        asyncio.run(_acquire_companion_turn_lease(db, request, "conversation:key"))

    assert raised.value.status_code == 503
    assert db.rollbacks == 1
    assert not getattr(request.state, "companion_turn_leases", [])


def test_turn_lease_release_is_scoped_to_the_request_owner():
    db = _Postgres([])
    request = _request()
    request.state.companion_turn_leases = ["idempotency:key", "conversation:key"]
    request.state.companion_turn_lease_owner = "owner-token"

    _release_companion_turn_leases(db, request)

    assert db.commits == 1
    assert len(db.statements) == 2
    assert all("owner_token = :owner_token" in sql for sql, _ in db.statements)
    assert all(params["owner_token"] == "owner-token" for _, params in db.statements)
    assert request.state.companion_turn_leases == []


def test_turn_lease_release_is_noop_without_owned_leases():
    db = _Postgres([])
    request = _request()

    _release_companion_turn_leases(db, request)

    assert db.commits == 0
    assert db.statements == []
