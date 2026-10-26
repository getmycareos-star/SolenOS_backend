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


def test_background_classifier_rejects_community(monkeypatch):
    monkeypatch.setattr(
        worker,
        "qwen_chat",
        lambda *args, **kwargs: json.dumps({"workflow": "community"}),
    )
    with pytest.raises(ValueError, match="community_disabled"):
        asyncio.run(worker._classify_task("Show me caregiver stories", ""))


def test_background_classifier_rejects_malformed_output(monkeypatch):
    monkeypatch.setattr(worker, "qwen_chat", lambda *args, **kwargs: "I think this is daily_capture")
    with pytest.raises(ValueError, match="workflow_classification_invalid"):
        asyncio.run(worker._classify_task("Help with care notes", ""))



@pytest.mark.parametrize(("expected", "task"), [
    ("daily_capture", "I want to record today's observation."),
    ("add_record", "Help draft a care record correction; the date is missing."),
    ("document", "Explain the document text supplied here."),
    ("living_care_record", "Summarize the current Living Care Record."),
    ("care_history_search", "Search care history for an earlier fall."),
    ("care_situation_change", "Compare current and prior care situation."),
    ("delta_engine", "Review possible changes or contradictions against existing care evidence."),
    ("evidence_provenance", "Identify the source and provenance of this claim."),
    ("uncertainty_contradictions", "Find uncertainty or contradictions in these care notes."),
    ("returning_caregiver", "Orient me as a returning caregiver."),
    ("appointment", "Help prepare appointment questions."),
    ("after_appointment", "Organize after-appointment notes."),
    ("handoff", "Draft a care handoff."),
    ("professional_summary", "Prepare a professional-facing care summary."),
    ("open_loops", "Identify open follow-ups and unresolved questions."),
    ("two_week_review", "Review care events from the past fourteen days."),
    ("decision_outcome", "Separate a care decision from its outcome."),
    ("medication_day", "Organize medication-day observations."),
    ("reminders_notifications", "Review my reminders and notifications."),
    ("care_coordination", "Organize care responsibilities and coordination."),
    ("companion", "I feel overwhelmed. Help me work out the next practical step."),
    ("caregiver_feedback", "Help me phrase a correction to a care record."),
    ("sharing_export", "Prepare a shareable care summary draft."),
    ("roles_access", "Explain who has access to this care record."),
    ("account_recovery", "Explain the safe account recovery steps."),
    ("settings_security", "Explain my privacy and security settings."),
    ("record_lifecycle", "Explain what archiving or restoring a care record means."),
    ("offline_sync", "Check whether my offline changes have synced."),
    ("ai_reliability", "Explain the current Qwen availability or retry state."),
    ("audit_review", "Summarize audit and review candidates."),
    ("care_transition", "Help me reconcile the hospital discharge summary with the current care record."),
])
def test_clear_requests_route_to_the_correct_internal_workflow(expected, task, monkeypatch):
    def should_not_call_model(*args, **kwargs):
        raise AssertionError("High-confidence workflow routing should not depend on the small model classifier")
    monkeypatch.setattr(worker, "qwen_chat", should_not_call_model)
    assert asyncio.run(worker._classify_task(task, "")) == expected


def test_ambiguous_request_falls_back_to_constrained_qwen_classification(monkeypatch):
    seen = {}
    def classify(*args, **kwargs):
        seen.update(kwargs)
        return json.dumps({"workflow": "companion"})
    monkeypatch.setattr(worker, "qwen_chat", classify)
    assert asyncio.run(worker._classify_task("Can you help me with this?", "")) == "companion"
    assert seen["response_format"]["properties"]["workflow"]["enum"] == list(worker.ENABLED_WORKFLOWS)

def test_enabled_workflow_list_has_unique_entries_and_no_community():
    assert len(worker.ENABLED_WORKFLOWS) >= 30
    assert len(set(worker.ENABLED_WORKFLOWS)) == len(worker.ENABLED_WORKFLOWS)
    assert "community" not in worker.ENABLED_WORKFLOWS

def test_background_classifier_requests_constrained_json_schema(monkeypatch):
    seen = {}

    def classify(*args, **kwargs):
        seen.update(kwargs)
        return json.dumps({"workflow": "daily_capture"})

    monkeypatch.setattr(worker, "qwen_chat", classify)
    assert asyncio.run(worker._classify_task("Please help me organize the information I gave you.", "")) == "daily_capture"
    schema = seen["response_format"]
    assert schema["type"] == "object"
    assert schema["properties"]["workflow"]["enum"] == list(worker.ENABLED_WORKFLOWS)
    assert schema["additionalProperties"] is False


def test_background_classifier_rejects_non_object_json(monkeypatch):
    monkeypatch.setattr(worker, "qwen_chat", lambda *args, **kwargs: json.dumps(["daily_capture"]))
    with pytest.raises(ValueError, match="workflow_classification_invalid"):
        asyncio.run(worker._classify_task("Help with care notes", ""))


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


@pytest.mark.parametrize(("expected", "task"), [
    ("care_situation_change", "What has changed between the current and previous care situation?"),
    ("decision_outcome", "What was decided and what happened afterward?"),
    ("medication_day", "Help organize today's medication log and related observations."),
    ("care_coordination", "Who is responsible for each care task? Help coordinate responsibilities between caregivers."),
    ("companion", "I'm overwhelmed by caregiving. Help me think through this."),
    ("caregiver_feedback", "Help me give feedback on a care record and phrase a correction."),
    ("roles_access", "Which caregivers can access this record?"),
    ("account_recovery", "I can't access my account because I forgot my password."),
    ("settings_security", "Help me manage privacy controls and account security options."),
    ("record_lifecycle", "How do I restore an archived record?"),
    ("ai_reliability", "Qwen is not responding. Check model availability and retry state."),
    ("audit_review", "Review the audit log and summarize recorded actions."),
])
def test_twelve_previously_misrouted_workflows_have_specific_intent_routes(expected, task, monkeypatch):
    def should_not_call_model(*args, **kwargs):
        raise AssertionError("Specific workflow intent should route without relying on small-model guessing")
    monkeypatch.setattr(worker, "qwen_chat", should_not_call_model)
    assert asyncio.run(worker._classify_task(task, "")) == expected
