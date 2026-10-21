"""Test-client support for authenticated API integration tests.

This does not bypass production authorization. It gives legacy domain tests a real,
synthetic account and care-circle memberships so requests exercise the same middleware.
Tests of denial behavior pass an explicit Authorization header.
"""
import json
import uuid
from urllib.parse import parse_qs, urlsplit

import fastapi.testclient
from fastapi.testclient import TestClient as _OriginalTestClient

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.security import issue_access_token
from app.models.auth import Account, CareMembership
from app.models.care import Caregiver, Person

get_settings().secret_key = "test-only-secret-key-not-for-deployment"


def _walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


class AuthenticatedTestClient(_OriginalTestClient):
    def __init__(self, app, *args, **kwargs):
        self._test_account_id = str(uuid.uuid4())
        self._test_email = "test-client-" + self._test_account_id + "@example.com"
        super().__init__(app, *args, **kwargs)

    def _seed_authorization(self, url, kwargs):
        db = SessionLocal()
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
            person_ids.update(
                value for value in parse_qs(parsed.query).get("person_id", []) if value
            )
            parts = [part for part in path.split("/") if part]
            for part in parts:
                if len(part) >= 6 and any(ch.isdigit() for ch in part) or part.startswith("person-"):
                    if db.query(Person).filter(Person.id == part).first():
                        person_ids.add(part)

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
                    person = Person(id=person_id, name="Synthetic integration-test person")
                    db.add(person)
                    db.flush()

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
                        exists = db.query(CareMembership).filter(
                            CareMembership.account_id == self._test_account_id,
                            CareMembership.person_id == person_id,
                            CareMembership.caregiver_id == caregiver.id,
                        ).first()
                        if not exists:
                            db.add(CareMembership(
                                account_id=self._test_account_id,
                                person_id=person_id,
                                caregiver_id=caregiver.id,
                                role="caregiver",
                                permissions='["read","write"]',
                                status="active",
                            ))

                linked = db.query(CareMembership).filter(
                    CareMembership.account_id == self._test_account_id,
                    CareMembership.person_id == person_id,
                ).count()
                if not linked:
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
                    db.add(CareMembership(
                        account_id=self._test_account_id,
                        person_id=person_id,
                        caregiver_id=caregiver.id,
                        role="caregiver",
                        permissions='["read","write"]',
                        status="active",
                    ))
            db.commit()
            return issue_access_token(self._test_account_id, "0")[0]
        finally:
            db.close()

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        has_explicit_authorization = any(key.lower() == "authorization" for key in headers)
        if not has_explicit_authorization:
            headers["Authorization"] = "Bearer " + self._seed_authorization(url, kwargs)
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


fastapi.testclient.TestClient = AuthenticatedTestClient
