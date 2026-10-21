"""Authenticated integration-test client; production authorization remains enabled."""
import uuid
from urllib.parse import parse_qs, urlsplit

import fastapi.testclient
from fastapi.testclient import TestClient as _OriginalTestClient

from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.core.security import issue_access_token
from app.main import app
from app.models.auth import Account, CareMembership
from app.models.care import Caregiver, Person
from app.core.request_authorization import _person_for_identifier

get_settings().secret_key = "test-only-secret-key-not-for-deployment"


def _open_db():
    override = app.dependency_overrides.get(get_db)
    if override is not None:
        resource = override()
        if hasattr(resource, "__next__"):
            return next(resource), resource.close
        return resource, lambda: None
    db = SessionLocal()
    return db, db.close


def _walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _ensure_account(db, account_id: str):
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        account = Account(
            id=account_id,
            email="test-client-" + account_id + "@example.com",
            password_hash="test-only-unused-password-hash",
            display_name="Integration Test User",
            token_version="0",
            is_active=True,
        )
        db.add(account)
        db.commit()
    return account


class AuthenticatedTestClient(_OriginalTestClient):
    def __init__(self, app_instance, *args, **kwargs):
        self._test_account_id = str(uuid.uuid4())
        super().__init__(app_instance, *args, **kwargs)

    def _seed_authorization(self, url, kwargs):
        db, close_db = _open_db()
        try:
            parsed = urlsplit(url)
            path = parsed.path
            if path.startswith("/api/v1/auth/"):
                _ensure_account(db, self._test_account_id)
                return issue_access_token(self._test_account_id, "0")[0]

            payload = kwargs.get("json") or {}
            person_ids = {
                str(value)
                for key, value in _walk(payload)
                if key in {"person_id", "personId"} and isinstance(value, str) and value
            }
            person_ids.update(value for value in parse_qs(parsed.query).get("person_id", []) if value)
            request_params = kwargs.get("params") or {}
            if isinstance(request_params, dict) and request_params.get("person_id"):
                person_ids.add(str(request_params["person_id"]))
            for part in (segment for segment in path.split("/") if segment):
                if len(part) >= 6:
                    resolved = _person_for_identifier(db, part)
                    if resolved:
                        person_ids.add(resolved)

            caregiver_ids = {
                str(value)
                for key, value in _walk(payload)
                if key in {
                    "caregiver_id",
                    "caregiverId",
                    "created_by_caregiver_id",
                    "uploaded_by_caregiver_id",
                } and isinstance(value, str) and value
            }

            # Simulate separate authenticated caregiver accounts when a test acts as
            # a named caregiver. The production middleware and membership checks still run.
            account_id = self._test_account_id
            if len(person_ids) == 1 and len(caregiver_ids) == 1:
                caregiver_id = next(iter(caregiver_ids))
                if not caregiver_id.startswith(("not-linked", "not-the-authenticated")):
                    identity = self._test_account_id + ":" + next(iter(person_ids)) + ":" + caregiver_id
                    account_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
            _ensure_account(db, account_id)

            for person_id in person_ids:
                person = db.query(Person).filter(Person.id == person_id).first()
                if not person:
                    # Unknown recipients remain unknown; authorization should fail closed.
                    continue

                selected_caregivers = []
                for caregiver_id in caregiver_ids:
                    if caregiver_id.startswith(("not-linked", "not-the-authenticated")):
                        continue
                    caregiver = db.query(Caregiver).filter(Caregiver.id == caregiver_id).first()
                    if not caregiver:
                        caregiver = Caregiver(
                            id=caregiver_id,
                            name="Synthetic integration-test caregiver",
                            email="shadow-" + uuid.uuid4().hex + "@example.invalid",
                            relationship="family caregiver",
                            person_id=person_id,
                            timezone="UTC",
                        )
                        db.add(caregiver)
                        db.flush()
                    if caregiver.person_id == person_id:
                        selected_caregivers.append(caregiver)

                if not selected_caregivers:
                    existing_membership = db.query(CareMembership).filter(
                        CareMembership.account_id == account_id,
                        CareMembership.person_id == person_id,
                        CareMembership.status == "active",
                    ).first()
                    if existing_membership:
                        continue
                    caregiver = db.query(Caregiver).filter(Caregiver.person_id == person_id).first()
                    if not caregiver:
                        caregiver = Caregiver(
                            id="test-caregiver-" + uuid.uuid4().hex,
                            name="Synthetic integration-test caregiver",
                            email="shadow-" + uuid.uuid4().hex + "@example.invalid",
                            relationship="family caregiver",
                            person_id=person_id,
                            timezone="UTC",
                        )
                        db.add(caregiver)
                        db.flush()
                    selected_caregivers = [caregiver]

                for caregiver in selected_caregivers:
                    _ensure_membership(db, account_id, person_id, caregiver.id)
            db.commit()
            return issue_access_token(account_id, "0")[0]
        finally:
            close_db()

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        has_explicit_authorization = any(key.lower() == "authorization" for key in headers)
        if not has_explicit_authorization:
            headers["Authorization"] = "Bearer " + self._seed_authorization(url, kwargs)
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


def _ensure_membership(db, account_id: str, person_id: str, caregiver_id: str):
    exists = db.query(CareMembership).filter(
        CareMembership.account_id == account_id,
        CareMembership.person_id == person_id,
        CareMembership.status == "active",
    ).first()
    if not exists:
        db.add(CareMembership(
            account_id=account_id,
            person_id=person_id,
            caregiver_id=caregiver_id,
            role="caregiver",
            permissions='["read","write"]',
            status="active",
        ))
        db.flush()


fastapi.testclient.TestClient = AuthenticatedTestClient
