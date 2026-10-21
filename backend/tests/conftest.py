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


class AuthenticatedTestClient(_OriginalTestClient):
    def __init__(self, app_instance, *args, **kwargs):
        self._test_account_id = str(uuid.uuid4())
        self._test_email = "test-client-" + self._test_account_id + "@example.com"
        super().__init__(app_instance, *args, **kwargs)

    def _seed_authorization(self, url, kwargs):
        db, close_db = _open_db()
        try:
            account = db.query(Account).filter(Account.id == self._test_account_id).first()
            if not account:
                account = Account(
                    id=self._test_account_id,
                    email=self._test_email,
                    password_hash="test-only-unused-password-hash",
                    display_name="Integration Test User",
                    token_version="0",
                    is_active=True,
                )
                db.add(account)
                db.commit()

            parsed = urlsplit(url)
            path = parsed.path
            if path.startswith("/api/v1/auth/"):
                return issue_access_token(self._test_account_id, "0")[0]

            payload = kwargs.get("json") or {}
            person_ids = {
                str(value)
                for key, value in _walk(payload)
                if key in {"person_id", "personId"} and isinstance(value, str) and value
            }
            person_ids.update(value for value in parse_qs(parsed.query).get("person_id", []) if value)
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

            for person_id in person_ids:
                person = db.query(Person).filter(Person.id == person_id).first()
                if not person:
                    # Unknown recipients remain unknown; authorization should fail closed.
                    continue

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
                        _ensure_membership(db, self._test_account_id, person_id, caregiver.id)

                caregivers = db.query(Caregiver).filter(Caregiver.person_id == person_id).all()
                if not caregivers:
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
                    caregivers = [caregiver]
                for caregiver in caregivers:
                    _ensure_membership(db, self._test_account_id, person_id, caregiver.id)
            db.commit()
            return issue_access_token(self._test_account_id, "0")[0]
        finally:
            close_db()

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        has_explicit_authorization = any(key.lower() == "authorization" for key in headers)
        if not has_explicit_authorization:
            headers["Authorization"] = "Bearer " + self._seed_authorization(url, kwargs)
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


def _walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _ensure_membership(db, account_id: str, person_id: str, caregiver_id: str):
    exists = db.query(CareMembership).filter(
        CareMembership.account_id == account_id,
        CareMembership.person_id == person_id,
        CareMembership.caregiver_id == caregiver_id,
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


fastapi.testclient.TestClient = AuthenticatedTestClient
