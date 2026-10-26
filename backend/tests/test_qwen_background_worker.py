import asyncio
import json

import pytest

from app.services import qwen_background_worker as worker


def test_background_classifier_accepts_only_enabled_workflows(monkeypatch):
    monkeypatch.setattr(
        worker,
        "qwen_chat",
        lambda *args, **kwargs: json.dumps({"workflow": "daily_capture"}),
    )
    assert asyncio.run(worker._classify_task("I want to record today's observation", "")) == "daily_capture"


def test_background_classifier_requests_structured_enum_output(monkeypatch):
    captured = {}

    def fake_qwen_chat(*args, **kwargs):
        captured.update(kwargs)
        return json.dumps({"workflow": "care_history_search"})

    monkeypatch.setattr(worker, "qwen_chat", fake_qwen_chat)
    result = asyncio.run(worker._classify_task("Find the last recorded care update", ""))
    assert result == "care_history_search"
    schema = captured["response_format"]
    assert schema["properties"]["workflow"]["enum"] == list(worker.ENABLED_WORKFLOWS)
    assert schema["required"] == ["workflow"]
    assert schema["additionalProperties"] is False


def test_background_classifier_rejects_community(monkeypatch):
    monkeypatch.setattr(
        worker,
        "qwen_chat",
        lambda *args, **kwargs: json.dumps({"workflow": "community"}),
    )
    with pytest.raises(ValueError, match="workflow_classification_invalid"):
        asyncio.run(worker._classify_task("Show me caregiver stories", ""))


def test_background_classifier_rejects_malformed_output(monkeypatch):
    monkeypatch.setattr(worker, "qwen_chat", lambda *args, **kwargs: "I think this is daily_capture")
    with pytest.raises(ValueError, match="workflow_classification_invalid"):
        asyncio.run(worker._classify_task("Help with care notes", ""))


def test_enabled_workflow_list_has_30_unique_entries_and_no_community():
    assert len(worker.ENABLED_WORKFLOWS) == 30
    assert len(set(worker.ENABLED_WORKFLOWS)) == 30
    assert "community" not in worker.ENABLED_WORKFLOWS


from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.qwen_background_job import QwenBackgroundJob


def _isolated_job_store(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'qwen-jobs.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    QwenBackgroundJob.__table__.create(bind=engine)
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    return sessions


def _seed_job(sessions, *, status="queued", attempt_count=0, locked_at=None):
    db = sessions()
    job = QwenBackgroundJob(
        person_id="person-test",
        caregiver_id="caregiver-test",
        task_text="Please summarize the supplied appointment note.",
        context_text="Appointment date: not supplied.",
        status=status,
        attempt_count=attempt_count,
        locked_at=locked_at,
        available_at=datetime.now(timezone.utc) - timedelta(minutes=20),
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()
    return job_id


def test_background_job_retries_and_fails_closed_after_bounded_attempts(tmp_path, monkeypatch):
    sessions = _isolated_job_store(tmp_path, monkeypatch)
    job_id = _seed_job(sessions, attempt_count=2)

    async def classify(*args, **kwargs):
        return "appointment"

    async def fail_assist(*args, **kwargs):
        raise HTTPException(status_code=503, detail="grounded response unavailable")

    monkeypatch.setattr(worker, "_classify_task", classify)
    from app.api import collaboration
    monkeypatch.setattr(collaboration, "qwen_support_assist", fail_assist)

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert job.status == "failed"
    assert job.attempt_count == 3
    assert job.result_text is None
    assert job.error_code
    db.close()


def test_worker_recovers_stale_processing_job_after_restart(tmp_path, monkeypatch):
    sessions = _isolated_job_store(tmp_path, monkeypatch)
    stale_lock = datetime.now(timezone.utc) - timedelta(minutes=20)
    job_id = _seed_job(sessions, status="processing", locked_at=stale_lock)

    async def classify(*args, **kwargs):
        return "appointment"

    async def complete_assist(payload, request, db):
        assert payload.workflow == "appointment"
        assert request.state.scoped_person_id == "person-test"
        return {
            "workflow": "appointment",
            "reply": "The appointment date was not supplied.",
            "source_event_ids": [],
            "source_evidence_ids": [],
        }

    monkeypatch.setattr(worker, "_classify_task", classify)
    from app.api import collaboration
    monkeypatch.setattr(collaboration, "qwen_support_assist", complete_assist)

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert job.status == "completed"
    assert job.workflow == "appointment"
    assert job.result_text == "The appointment date was not supplied."
    assert job.completed_at is not None
    assert job.locked_at is None
    db.close()
