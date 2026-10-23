from datetime import datetime, timezone
import json
import pytest
from fastapi import HTTPException

from app.services import delta_engine


class FakeResponse:
    def raise_for_status(self):
        return None
    def json(self):
        return {"message": {"content": json.dumps({
            "event_summary": "Medication review",
            "medications": [{"change_type": "dose_changed", "name": "metformin", "previous": "500 mg", "current": "1000 mg", "evidence_excerpt": "Increase metformin to 1000 mg", "certainty": "supported"}],
            "conditions": [], "symptoms": [], "tests": [{"name": "MRI", "status": "ordered", "ordered_date": "2026-10-22", "result_present": False, "evidence_excerpt": "MRI ordered"}],
            "follow_ups": [{"action": "Book follow-up", "status": "pending", "due_date": None, "owner": None, "evidence_excerpt": "follow-up in 2 weeks"}],
            "contradictions": [], "clinical_gaps": [{"gap": "MRI result not in supplied record", "why_it_matters": "Result not available", "next_question": "Has the MRI been completed?", "evidence_excerpt": "MRI ordered"}],
            "caregiver_summary": "A medication dose change was reported. The MRI result is not present in this event.",
            "clinician_summary": "Reported metformin dose change; MRI result not present in supplied event."
        })}}


def test_invalid_model_json_fails_closed(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        delta_engine._parse_model_json("not json")
    assert exc.value.status_code == 502


def test_missing_local_model_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    with pytest.raises(HTTPException) as exc:
        delta_engine._local_ollama_url()
    assert exc.value.status_code == 503


def test_local_model_config_rejects_loopback_in_production(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    with pytest.raises(HTTPException):
        delta_engine._local_ollama_url()


def test_delta_contract_keeps_missing_result_and_follow_up_open(monkeypatch):
    parsed = delta_engine._parse_model_json(FakeResponse().json()["message"]["content"])
    assert parsed["tests"][0]["result_present"] is False
    assert parsed["follow_ups"][0]["status"] == "pending"
    assert parsed["clinical_gaps"][0]["next_question"] == "Has the MRI been completed?"


def test_delta_run_migration_adds_durable_history_table():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import Column, MetaData, String, Table, inspect, create_engine

    engine = create_engine("sqlite://")
    metadata = MetaData()
    Table("persons", metadata, Column("id", String, primary_key=True))
    Table("care_events", metadata, Column("id", String, primary_key=True))
    metadata.create_all(engine)
    path = Path(__file__).parents[1] / "alembic" / "versions" / "20261023_04_delta_runs.py"
    spec = importlib.util.spec_from_file_location("delta_runs_migration", path)
    migration = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
    inspector = inspect(engine)
    assert inspector.has_table("care_delta_runs")
    columns = {column["name"] for column in inspector.get_columns("care_delta_runs")}
    assert {"person_id", "delta_json", "source_event_id", "evidence_ids", "model_name"} <= columns
    engine.dispose()


def test_reconcile_persists_result_and_open_loops(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.core.database import Base
    from app.models.care import Person
    from app.models.care_continuity import CareAssertion, CareDeltaRun
    from app.models.uncertainty import InformationGap, OpenQuestion

    class Client:
        def __init__(self, timeout): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://model.internal:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setattr(delta_engine.httpx, "Client", Client)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(Person(id="delta-person", name="Care recipient"))
    db.commit()
    try:
        result = delta_engine.reconcile_event(
            db, person_id="delta-person", event_date=datetime(2026, 10, 22, tzinfo=timezone.utc),
            source="Visit note", content="Increase metformin to 1000 mg. MRI ordered. follow-up in 2 weeks.",
            created_by_caregiver_id="caregiver-from-session",
        )
        saved = db.query(CareDeltaRun).filter_by(person_id="delta-person").one()
        assert saved.id == result["provenance"]["delta_run_id"]
        assert "event_summary" in json.loads(saved.delta_json)
        assert saved.created_by_caregiver_id == "caregiver-from-session"
        assert db.query(CareAssertion).filter_by(person_id="delta-person").count() == 1
        assert db.query(InformationGap).filter_by(person_id="delta-person").count() >= 3
        assert db.query(OpenQuestion).filter_by(person_id="delta-person", status="open").count() >= 3
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_delta_parser_rejects_malformed_nested_items_before_persistence():
    payload = json.loads(FakeResponse().json()["message"]["content"])
    del payload["medications"][0]["evidence_excerpt"]
    with pytest.raises(HTTPException) as exc:
        delta_engine._parse_model_json(json.dumps(payload))
    assert exc.value.status_code == 502


def test_delta_parser_rejects_wrong_nested_field_types():
    payload = json.loads(FakeResponse().json()["message"]["content"])
    payload["tests"][0]["result_present"] = "false"
    with pytest.raises(HTTPException) as exc:
        delta_engine._parse_model_json(json.dumps(payload))
    assert exc.value.status_code == 502


def test_unchanged_findings_are_not_promoted_to_new_active_assertions(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.core.database import Base
    from app.models.care import Person
    from app.models.care_continuity import CareAssertion

    payload = json.loads(FakeResponse().json()["message"]["content"])
    payload["medications"][0].update({
        "change_type": "unchanged", "previous": "1000 mg", "current": "1000 mg",
        "evidence_excerpt": "Increase metformin to 1000 mg",
    })

    class Response:
        def raise_for_status(self): return None
        def json(self): return {"message": {"content": json.dumps(payload)}}
    class Client:
        def __init__(self, timeout): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, *args, **kwargs): return Response()

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://model.internal:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setattr(delta_engine.httpx, "Client", Client)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(Person(id="unchanged-person", name="Care recipient"))
    db.commit()
    try:
        delta_engine.reconcile_event(
            db, person_id="unchanged-person", event_date=datetime(2026, 10, 22, tzinfo=timezone.utc),
            source="Visit note", content="Increase metformin to 1000 mg. MRI ordered. follow-up in 2 weeks.",
        )
        assert db.query(CareAssertion).filter_by(person_id="unchanged-person").count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_local_model_config_rejects_public_host_in_production(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://public-model.example.com")
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    with pytest.raises(HTTPException) as exc:
        delta_engine._local_ollama_url()
    assert exc.value.status_code == 503


def test_delta_receives_existing_open_loops_as_context_not_verified_facts(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.core.database import Base
    from app.models.care import Person
    from app.models.uncertainty import InformationGap, OpenQuestion, Contradiction, GapLifecycleStatus, GapReason, EpistemicState, Priority

    captured = {}
    class Response:
        def raise_for_status(self): return None
        def json(self): return FakeResponse().json()
    class Client:
        def __init__(self, timeout): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, url, json, **kwargs):
            captured.update(json)
            return Response()

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://model.internal:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr(delta_engine.httpx, "Client", Client)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(Person(id="loop-person", name="Care recipient"))
    db.commit()
    db.add(InformationGap(
        person_id="loop-person", subject_type="test", subject_id="MRI",
        field="result_missing", epistemic_state=EpistemicState.UNKNOWN,
        gap_reason=GapReason.ABSENT, priority=Priority.HIGH,
        lifecycle_status=GapLifecycleStatus.OPEN, description="MRI result not present",
    ))
    db.add(OpenQuestion(
        person_id="loop-person", subject_type="test", subject_id="MRI",
        question="Has the MRI result been received?", status="open",
        priority=Priority.HIGH,
    ))
    db.add(Contradiction(
        person_id="loop-person", subject_type="medication", subject_id="metformin",
        field="medication_status", description="Two sources disagree about metformin",
        resolution_status="unresolved",
    ))
    db.commit()
    try:
        delta_engine.reconcile_event(
            db, person_id="loop-person", event_date=datetime(2026, 10, 22, tzinfo=timezone.utc),
            source="Visit note", content="MRI ordered. Increase metformin to 1000 mg. follow-up in 2 weeks.",
            persist=False,
        )
        payload = json.loads(captured["messages"][-1]["content"])
        loops = payload["existing_unresolved_care_loops"]
        assert any(item["subject_id"] == "MRI" for item in loops["gaps"])
        assert any("MRI result" in item["question"] for item in loops["questions"])
        assert any(item["subject_id"] == "metformin" for item in loops["contradictions"])
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_delta_does_not_persist_value_unmatched_to_source_even_with_valid_quote(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.core.database import Base
    from app.models.care import Person
    from app.models.care_continuity import CareAssertion

    payload = json.loads(FakeResponse().json()["message"]["content"])
    payload["medications"][0]["current"] = "900 mg"

    class Response:
        def raise_for_status(self): return None
        def json(self): return {"message": {"content": json.dumps(payload)}}
    class Client:
        def __init__(self, timeout): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, url, json, **kwargs): return Response()

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://model.internal:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr(delta_engine.httpx, "Client", Client)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(Person(id="unsupported-value-person", name="Care recipient"))
    db.commit()
    try:
        result = delta_engine.reconcile_event(
            db, person_id="unsupported-value-person", event_date=datetime(2026, 10, 22, tzinfo=timezone.utc),
            source="Visit note", content="Increase metformin to 1000 mg. MRI ordered. follow-up in 2 weeks.",
        )
        assert result["medications"][0]["evidence_supported"] is True
        assert result["medications"][0]["claim_supported"] is False
        assert result["medications"][0]["certainty"] == "uncertain"
        assert db.query(CareAssertion).filter_by(person_id="unsupported-value-person").count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
