import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.care import Caregiver, Evidence, Person


@pytest.fixture(autouse=True)
def reset_app_state():
    app.dependency_overrides = {}
    yield
    app.dependency_overrides = {}


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
        Base.metadata.drop_all(bind=engine)


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
            uploaded_by_caregiver_id=caregiver.id,
            source_type="caregiver",
            evidence_status="reported",
        )
        db.add(record)
        db.commit()
        return record

    return _make
