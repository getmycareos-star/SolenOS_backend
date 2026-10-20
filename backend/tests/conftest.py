import os
os.environ["ALLOWED_HOSTS"] = "testserver,localhost,127.0.0.1"

from app.core.config import get_settings
get_settings.cache_clear()

from app.main import app, api_limiter, auth_limiter, strict_limiter

# Capture the production route table immediately after importing the app,
# before test modules are imported during collection. Capturing at collection
# finish is unsafe: module-level test setup can already have mutated the global
# app by then, turning a damaged route table into the supposed baseline.
APP_ROUTES_BASELINE = list(app.router.routes)

import pytest

from sqlalchemy import create_engine

from sqlalchemy.orm import sessionmaker

from sqlalchemy.pool import StaticPool


from app.core.database import Base

from app.models.caregiver import Caregiver
from app.models.evidence import Evidence
from app.models.identity import Person


@pytest.fixture(autouse=True)
def reset_app_state():
    # Restore only from the snapshot captured before test-module imports.
    # Never replace a known-good baseline with the already-mutated live table.

    # Dependency overrides, route registration and rate counters are
    # process-global. Restore the complete route table so one test cannot
    # contaminate another.
    app.router.routes[:] = list(APP_ROUTES_BASELINE)
    app.dependency_overrides = {}
    for limiter in (api_limiter, auth_limiter, strict_limiter):
        limiter.requests.clear()
    yield
    app.router.routes[:] = list(APP_ROUTES_BASELINE)
    app.dependency_overrides = {}
    for limiter in (api_limiter, auth_limiter, strict_limiter):
        limiter.requests.clear()


@pytest.fixture(autouse=True)
def disable_local_model(monkeypatch):
    """Keep the test suite off the local model runtime.

    Without this every service would construct a real engine and wait on a
    model call. Tests that want model output inject a fake client explicitly.
    """
    from app.core.config import get_settings
    from app.services.llm.engine import LocalReasoningEngine

    monkeypatch.setenv("LLM_ENABLED", "false")
    get_settings.cache_clear()
    LocalReasoningEngine.reset_circuit()
    yield
    get_settings.cache_clear()
    LocalReasoningEngine.reset_circuit()


@pytest.fixture()
def db():
    """An isolated in-memory database for one test.

    Mirrors the pattern the existing suite uses, but as a fixture so the newer
    tests do not each have to repeat the teardown. Created per test so rows
    never leak between cases.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db):
    """Test client wired to the per-test in-memory database.

    The app's production engine must not be used by API tests: otherwise test
    cases leak state into a local SQLite file and results depend on execution
    order.
    """
    from fastapi.testclient import TestClient
    from app.core.database import get_db

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


@pytest.fixture()
def person(db):
    record = Person(id="p1", name="Margaret")
    db.add(record)
    db.commit()
    return record


@pytest.fixture()
def caregiver(db):
    record = Caregiver(
        id="c1",
        name="Jo",
        email="jo@example.com",
        relationship="daughter",
        person_id="p1",
    )
    db.add(record)
    db.commit()
    return record


@pytest.fixture()
def evidence(db, caregiver):
    def _make(evidence_id="e1", text="Mum was up four times last night.", person_id="p1"):
        record = Evidence(
            id=evidence_id,
            person_id=person_id,
            type="caregiver_report",
            source_text=text,
            subject_role="care_recipient",
            subject_id=person_id,
            entity_resolution_status="resolved",
            observer_role="caregiver",
            observer_id=caregiver.id,
            author_role="caregiver",
            author_id=caregiver.id,
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver",
            evidence_status="reported",
        )
        db.add(record)
        db.commit()
        return record

    return _make
