import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.qwen_background_job import QwenBackgroundJob, QwenJobEvent
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



def test_worker_does_not_persist_assistance_if_job_is_cancelled_during_inference(tmp_path, monkeypatch):
    from app.models.qwen_background_job import QwenModelOutput

    sessions = _isolated_job_store(tmp_path, monkeypatch)
    job_id = _seed_job(sessions)
    caller_thread_id = threading.get_ident()

    async def classify(*args, **kwargs):
        return "appointment"

    def cancel_assist(payload, request, inference_db):
        assert threading.get_ident() != caller_thread_id
        assert payload.workflow == "appointment"
        current = inference_db.query(QwenBackgroundJob).filter_by(id=job_id).one()
        assert current.status == "processing"
        current.status = "cancelled"
        current.error_code = "document_deleted"
        inference_db.commit()
        return {}

    monkeypatch.setattr(worker, "_classify_task", classify)
    from app.api import collaboration
    monkeypatch.setattr(collaboration, "qwen_support_assist", cancel_assist)

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    cancelled = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert cancelled.status == "cancelled"
    assert cancelled.error_code == "document_deleted"
    assert db.query(QwenModelOutput).filter_by(job_id=job_id).count() == 0
    assert db.query(QwenJobEvent).filter_by(job_id=job_id, event_type="job_completed").count() == 0
    db.close()


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

from app.models.qwen_background_job import QwenBackgroundJob, QwenModelOutput, QwenJobEvent, QwenSafetyEvaluation


def _isolated_job_store(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'qwen-jobs.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    QwenBackgroundJob.__table__.create(bind=engine)
    QwenModelOutput.__table__.create(bind=engine)
    QwenJobEvent.__table__.create(bind=engine)
    QwenSafetyEvaluation.__table__.create(bind=engine)
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

    def fail_assist(*args, **kwargs):
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


@pytest.mark.parametrize(
    ("detail", "expected_status", "expected_error_code"),
    [
        (
            {"code": "runtime_unreachable", "reason": "ConnectionRefusedError"},
            "queued",
            "runtime_unreachable",
        ),
        (
            {"code": "unsupported_care_claim"},
            "failed",
            "unsupported_care_claim",
        ),
    ],
)
def test_worker_handles_structured_qwen_failures_without_sticking_jobs(
    tmp_path, monkeypatch, detail, expected_status, expected_error_code
):
    sessions = _isolated_job_store(tmp_path, monkeypatch)
    job_id = _seed_job(sessions)

    async def classify(*args, **kwargs):
        return "appointment"

    def fail_assist(*args, **kwargs):
        raise HTTPException(status_code=503, detail=detail)

    monkeypatch.setattr(worker, "_classify_task", classify)
    from app.api import collaboration
    monkeypatch.setattr(collaboration, "qwen_support_assist", fail_assist)

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert job.status == expected_status
    assert job.attempt_count == 1
    assert job.error_code == expected_error_code
    assert job.result_text is None
    assert db.query(QwenModelOutput).filter_by(job_id=job_id).count() == 0
    db.close()


def test_worker_lease_refresh_extends_only_the_matching_processing_job(tmp_path, monkeypatch):
    sessions = _isolated_job_store(tmp_path, monkeypatch)
    job_id = _seed_job(
        sessions,
        status="processing",
        locked_at=datetime.now(timezone.utc) - timedelta(minutes=9),
    )

    refreshed_at = datetime.now(timezone.utc)
    assert worker._refresh_qwen_job_lease_once(
        job_id,
        "person-test",
        "caregiver-test",
        now=refreshed_at,
    ) is True

    db = sessions()
    job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert job.status == "processing"
    persisted_lock = job.locked_at
    if persisted_lock.tzinfo is None:
        persisted_lock = persisted_lock.replace(tzinfo=timezone.utc)
    assert persisted_lock == refreshed_at
    db.close()

    # A cancelled job or a mismatched identity must never be resurrected.
    db = sessions()
    job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    job.status = "cancelled"
    db.commit()
    db.close()

    assert worker._refresh_qwen_job_lease_once(
        job_id,
        "person-test",
        "caregiver-test",
    ) is False
    assert worker._refresh_qwen_job_lease_once(
        job_id,
        "person-test",
        "different-caregiver",
    ) is False


def test_worker_recovers_stale_processing_job_after_restart(tmp_path, monkeypatch):
    sessions = _isolated_job_store(tmp_path, monkeypatch)
    caller_thread_id = threading.get_ident()
    stale_lock = datetime.now(timezone.utc) - timedelta(minutes=20)
    job_id = _seed_job(sessions, status="processing", locked_at=stale_lock)

    async def classify(*args, **kwargs):
        return "appointment"

    def complete_assist(payload, request, db):
        assert threading.get_ident() != caller_thread_id
        assert payload.workflow == "appointment"
        assert request.state.scoped_person_id == "person-test"
        return {
            "workflow": "appointment",
            "reply": "The appointment date was not supplied.",
            "ai_generated": True,
            "provider_status": "available",
            "model": "qwen3.5:4b",
            "source_event_ids": [],
            "source_evidence_ids": [],
            "source_appointment_ids": ["appointment-record-123456"],
            "source_handoff_ids": ["handoff-record-123456"],
            "source_notification_ids": ["notification-record-123456"],
            "source_freshness_assessment_ids": ["freshness-assessment-123456"],
            "source_freshness_status": "available",
            "claim_provenance": {
                "version": "claim-provenance-v1",
                "claims": [{
                    "claim_id": "statement-1",
                    "text": "The appointment date was not supplied.",
                    "support_status": "not_matched_to_saved_wording",
                    "source_references": [],
                }],
            },
        }

    monkeypatch.setattr(worker, "_classify_task", classify)
    from app.api import collaboration
    monkeypatch.setattr(collaboration, "qwen_support_assist", complete_assist)

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert job.status == "completed"
    assert job.workflow == "appointment"
    assert job.result_text is None
    output = db.query(QwenModelOutput).filter_by(job_id=job_id).one()
    assert output.output_text == "The appointment date was not supplied."
    assert output.model_name == "qwen3.5:4b"
    assert output.output_metadata["inference_provider"] == "ollama"
    assert output.output_metadata["model_name"] == "qwen3.5:4b"
    assert output.output_metadata["live_inference_confirmed"] is True
    assert output.review_status == "pending_review"
    assert output.review_required is True
    assert output.written_to_lcr is False
    assert output.output_metadata["source_provenance_version"] == "qwen-source-records-v1"
    assert output.output_metadata["source_appointment_ids"] == ["appointment-record-123456"]
    assert output.output_metadata["source_handoff_ids"] == ["handoff-record-123456"]
    assert output.output_metadata["source_notification_ids"] == ["notification-record-123456"]
    assert output.output_metadata["source_freshness_assessment_ids"] == ["freshness-assessment-123456"]
    assert output.output_metadata["source_freshness_status"] == "available"
    assert output.output_metadata["claim_provenance"]["claims"][0]["support_status"] == "not_matched_to_saved_wording"
    safety = db.query(QwenSafetyEvaluation).filter_by(output_id=output.id).one()
    assert safety.decision == "review_required"
    assert safety.human_review_required is True
    assert db.query(QwenJobEvent).filter_by(job_id=job_id, event_type="job_completed").count() == 1
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


def test_worker_waits_quietly_when_job_table_is_not_yet_migrated(tmp_path, monkeypatch, caplog):
    # The production container may start before the migration step completes.
    # A missing queue table must not produce PostgreSQL/SQLAlchemy query errors.
    engine = create_engine(f"sqlite:///{tmp_path / 'unmigrated-qwen.sqlite'}")
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    try:
        with caplog.at_level("WARNING", logger=worker.logger.name):
            assert asyncio.run(worker.process_one_qwen_job()) is False
        assert "Qwen background job failed" not in caplog.text
    finally:
        engine.dispose()


def test_worker_processes_document_intake_job_without_qwen_output(tmp_path, monkeypatch):
    sessions = _isolated_job_store(tmp_path, monkeypatch)
    db = sessions()
    job = QwenBackgroundJob(
        person_id="person-test",
        caregiver_id="caregiver-test",
        task_text="Process saved document for caregiver review.",
        context_text="",
        evidence_ids_json=json.dumps(["evidence-document-1"]),
        workflow="document_intake",
        status="queued",
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    calls = []

    def process(evidence_id, person_id, caregiver_id):
        calls.append((evidence_id, person_id, caregiver_id))
        return (
            {"analysis_id": "analysis-1"},
            {"analysis_id": "analysis-1", "intelligence_status": "available", "observations": [{"observation": "candidate 1"}, {"observation": "candidate 2"}]},
        )

    monkeypatch.setattr(worker, "_process_document_evidence", process)
    monkeypatch.setattr(
        worker,
        "qwen_chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("document intake must not invoke a second Qwen workflow")),
    )

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    completed = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert completed.status == "completed"
    assert completed.workflow == "document_intake"
    result = json.loads(completed.result_text)
    assert result[0]["analysis_id"] == "analysis-1"
    assert result[0]["intelligence_status"] == "available"
    assert result[0]["candidate_count"] == 2
    assert calls == [("evidence-document-1", "person-test", "caregiver-test")]
    assert db.query(QwenModelOutput).filter_by(job_id=job_id).count() == 0
    event = db.query(QwenJobEvent).filter_by(job_id=job_id, event_type="document_intake_completed").one()
    assert event.metadata_json["review_required"] is True
    assert event.metadata_json["written_to_lcr"] is False
    db.close()


def test_worker_marks_permanent_document_extraction_errors_failed(tmp_path, monkeypatch):
    from fastapi import HTTPException

    sessions = _isolated_job_store(tmp_path, monkeypatch)
    job_id = _seed_job(sessions)
    db = sessions()
    job = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    job.workflow = "document_intake"
    job.evidence_ids_json = json.dumps(["evidence-unsupported"])
    db.commit()
    db.close()

    monkeypatch.setattr(
        worker,
        "_process_document_evidence",
        lambda *args, **kwargs: (_ for _ in ()).throw(HTTPException(status_code=415, detail="unsupported document")),
    )

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    failed = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert failed.status == "failed"
    assert failed.attempt_count == 1
    assert failed.error_code == "unsupported document"
    db.close()


def test_document_intake_requeues_remaining_page_windows_without_blocking_worker(tmp_path, monkeypatch):
    sessions = _isolated_job_store(tmp_path, monkeypatch)
    db = sessions()
    job = QwenBackgroundJob(
        person_id="person-test",
        caregiver_id="caregiver-test",
        task_text="Process saved document for caregiver review.",
        evidence_ids_json=json.dumps(["evidence-long-packet"]),
        workflow="document_intake",
        status="queued",
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    passes = {"count": 0}

    def process(evidence_id, person_id, caregiver_id):
        passes["count"] += 1
        if passes["count"] == 1:
            return (
                {"analysis_id": "analysis-long"},
                {
                    "analysis_id": "analysis-long",
                    "intelligence_status": "partial",
                    "observations": [{"observation": "page one candidate"}],
                    "processed_page_count": 12,
                    "total_page_count": 30,
                    "unprocessed_page_numbers": list(range(13, 31)),
                },
            )
        return (
            {"analysis_id": "analysis-long"},
            {
                "analysis_id": "analysis-long",
                "intelligence_status": "available",
                "observations": [{"observation": "page one candidate"}, {"observation": "page 30 candidate"}],
                "processed_page_count": 30,
                "total_page_count": 30,
                "unprocessed_page_numbers": [],
            },
        )

    monkeypatch.setattr(worker, "_process_document_evidence", process)
    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    queued = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert queued.status == "queued"
    assert queued.attempt_count == 0
    assert json.loads(queued.result_text)[0]["unprocessed_page_numbers"] == list(range(13, 31))
    queued.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    db.close()

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    completed = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert completed.status == "completed"
    assert passes["count"] == 2
    assert db.query(QwenJobEvent).filter_by(
        job_id=job_id, event_type="document_intake_page_window_completed"
    ).count() == 1
    assert db.query(QwenJobEvent).filter_by(
        job_id=job_id, event_type="document_intake_completed"
    ).count() == 1
    db.close()

def test_document_intake_finishes_later_windows_before_failing_an_exhausted_page(tmp_path, monkeypatch):
    sessions = _isolated_job_store(tmp_path, monkeypatch)
    db = sessions()
    job = QwenBackgroundJob(
        person_id="person-test",
        caregiver_id="caregiver-test",
        task_text="Process saved document for caregiver review.",
        evidence_ids_json=json.dumps(["evidence-with-one-unreadable-page"]),
        workflow="document_intake",
        status="queued",
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    passes = {"count": 0}

    def process(evidence_id, person_id, caregiver_id):
        passes["count"] += 1
        if passes["count"] == 1:
            return (
                {"analysis_id": "analysis-long", "retry_exhausted_page_numbers": [2]},
                {
                    "analysis_id": "analysis-long",
                    "intelligence_status": "partial",
                    "observations": [],
                    "processed_page_count": 12,
                    "total_page_count": 30,
                    "unprocessed_page_numbers": [2, *range(13, 31)],
                },
            )
        return (
            {"analysis_id": "analysis-long", "retry_exhausted_page_numbers": [2]},
            {
                "analysis_id": "analysis-long",
                "intelligence_status": "partial",
                "observations": [],
                "processed_page_count": 29,
                "total_page_count": 30,
                "unprocessed_page_numbers": [2],
            },
        )

    monkeypatch.setattr(worker, "_process_document_evidence", process)
    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    queued = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert queued.status == "queued"
    assert passes["count"] == 1
    assert db.query(QwenJobEvent).filter_by(
        job_id=job_id, event_type="document_intake_page_window_completed"
    ).count() == 1
    db.close()

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    failed = db.query(QwenBackgroundJob).filter_by(id=job_id).one()
    assert failed.status == "failed"
    assert failed.error_code == "document_page_retry_exhausted"
    assert passes["count"] == 2
    assert db.query(QwenJobEvent).filter_by(
        job_id=job_id, event_type="document_intake_page_retry_exhausted"
    ).count() == 1
    db.close()



def _daily_delta_retry_store(tmp_path, monkeypatch):
    from sqlalchemy.pool import StaticPool
    from app.core.database import Base
    from app.models.care import Caregiver, CareEvent, Evidence, Person
    from app.models.capture_analysis import CaptureAnalysis
    from app.models.care_continuity import CareStateRevision
    from app.models.qwen_background_job import QwenBackgroundJob, QwenJobEvent

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Person.__table__,
            Caregiver.__table__,
            Evidence.__table__,
            CareEvent.__table__,
            CaptureAnalysis.__table__,
            CareStateRevision.__table__,
            QwenBackgroundJob.__table__,
            QwenJobEvent.__table__,
        ],
    )
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", sessions)

    db = sessions()
    person = Person(id="daily-delta-person", name="Daily Delta Test")
    caregiver = Caregiver(
        id="daily-delta-caregiver", name="Test Caregiver",
        relationship="family", person_id=person.id,
    )
    evidence = Evidence(
        id="daily-delta-evidence", person_id=person.id, type="note",
        source_text="Caregiver reports poor sleep overnight.",
        uploaded_by_caregiver_id=caregiver.id,
        evidence_status="active",
    )
    event = CareEvent(
        id="daily-delta-event", person_id=person.id, event_type="daily_observation",
        status="recorded", occurred_at=datetime.now(timezone.utc),
        title="Daily care observation", description="Caregiver reports poor sleep overnight.",
        evidence_ids=[evidence.id], created_by_caregiver_id=caregiver.id,
    )
    analysis = CaptureAnalysis(
        id="daily-delta-analysis", person_id=person.id, caregiver_id=caregiver.id,
        source_event_id=event.id, source_evidence_id=evidence.id,
        source_text=event.description, extraction_status="available",
        candidates={"observations": []}, review_history=[],
        execution_status={"longitudinal_processing": "retrying"},
    )
    job = QwenBackgroundJob(
        id="daily-delta-job", person_id=person.id, caregiver_id=caregiver.id,
        task_text="Reconcile saved Daily Care evidence.", context_text="",
        workflow="daily_capture_delta_reconciliation", status="queued",
        attempt_count=1, available_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        source_event_ids_json=json.dumps([event.id]),
        source_evidence_ids_json=json.dumps([evidence.id]),
    )
    db.add_all([person, caregiver, evidence, event, analysis, job])
    db.commit()
    db.close()
    return sessions


def test_daily_capture_delta_retry_reuses_saved_sources_and_completes_without_reextraction(tmp_path, monkeypatch):
    from app.models.care import CareEvent, Evidence
    from app.models.capture_analysis import CaptureAnalysis

    sessions = _daily_delta_retry_store(tmp_path, monkeypatch)
    calls = []

    def reconcile(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"status": "failed", "error_code": "temporary_delta_failure"}
        return {"status": "completed", "run_id": "delta-run-reused-source", "reused": True}

    monkeypatch.setattr(worker, "_reconcile_daily_capture_delta", reconcile)

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    queued = db.query(QwenBackgroundJob).filter_by(id="daily-delta-job").one()
    assert queued.status == "queued"
    assert queued.attempt_count == 2
    assert queued.workflow == "daily_capture_delta_reconciliation"
    assert json.loads(queued.source_event_ids_json) == ["daily-delta-event"]
    assert json.loads(queued.source_evidence_ids_json) == ["daily-delta-evidence"]
    analysis = db.query(CaptureAnalysis).filter_by(id="daily-delta-analysis").one()
    assert analysis.execution_status["longitudinal_processing"] == "retrying"
    queued.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    db.close()

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    completed = db.query(QwenBackgroundJob).filter_by(id="daily-delta-job").one()
    assert completed.status == "completed"
    assert completed.attempt_count == 3
    result = json.loads(completed.result_text)
    assert result["delta_reconciliation_status"] == "completed"
    assert result["delta_run_id"] == "delta-run-reused-source"
    assert result["longitudinal_processing_status"] == "completed"
    analysis = db.query(CaptureAnalysis).filter_by(id="daily-delta-analysis").one()
    assert analysis.execution_status["longitudinal_processing"] == "completed"
    assert db.query(CareEvent).filter_by(id="daily-delta-event").count() == 1
    assert db.query(Evidence).filter_by(id="daily-delta-evidence").count() == 1
    assert len(calls) == 2
    assert all(call == {
        "person_id": "daily-delta-person",
        "caregiver_id": "daily-delta-caregiver",
        "source_event_id": "daily-delta-event",
        "source_evidence_id": "daily-delta-evidence",
    } for call in calls)
    db.close()


def test_daily_capture_delta_retry_stops_when_original_source_is_invalidated(tmp_path, monkeypatch):
    from app.models.care import CareEvent
    from app.models.capture_analysis import CaptureAnalysis

    sessions = _daily_delta_retry_store(tmp_path, monkeypatch)
    db = sessions()
    event = db.query(CareEvent).filter_by(id="daily-delta-event").one()
    event.status = "invalidated"
    db.commit()
    db.close()

    def must_not_reconcile(**kwargs):
        raise AssertionError("invalidated Daily Care sources must never be reconciled")

    monkeypatch.setattr(worker, "_reconcile_daily_capture_delta", must_not_reconcile)
    assert asyncio.run(worker.process_one_qwen_job()) is True

    db = sessions()
    cancelled = db.query(QwenBackgroundJob).filter_by(id="daily-delta-job").one()
    assert cancelled.status == "cancelled"
    assert cancelled.error_code == "daily_capture_source_unavailable"
    analysis = db.query(CaptureAnalysis).filter_by(id="daily-delta-analysis").one()
    assert analysis.execution_status["longitudinal_processing"] == "cancelled"
    assert db.query(QwenJobEvent).filter_by(
        job_id="daily-delta-job", event_type="daily_capture_delta_retry_cancelled"
    ).count() == 1
    db.close()


def test_daily_capture_delta_retry_fails_closed_after_three_total_attempts(tmp_path, monkeypatch):
    from app.models.capture_analysis import CaptureAnalysis

    sessions = _daily_delta_retry_store(tmp_path, monkeypatch)
    calls = []

    def fail_reconciliation(**kwargs):
        calls.append(kwargs)
        return {"status": "failed", "error_code": "temporary_delta_failure"}

    monkeypatch.setattr(worker, "_reconcile_daily_capture_delta", fail_reconciliation)

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    queued = db.query(QwenBackgroundJob).filter_by(id="daily-delta-job").one()
    assert queued.status == "queued"
    assert queued.attempt_count == 2
    queued.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    db.close()

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    failed = db.query(QwenBackgroundJob).filter_by(id="daily-delta-job").one()
    assert failed.status == "failed"
    assert failed.attempt_count == 3
    assert failed.error_code == "temporary_delta_failure"
    assert failed.completed_at is None
    analysis = db.query(CaptureAnalysis).filter_by(id="daily-delta-analysis").one()
    assert analysis.execution_status["longitudinal_processing"] == "failed"
    assert len(calls) == 2
    assert db.query(QwenJobEvent).filter_by(
        job_id="daily-delta-job", event_type="daily_capture_delta_reconciliation_retry"
    ).count() == 2
    db.close()


def test_daily_capture_delta_retry_completes_when_no_comparison_baseline_applies(tmp_path, monkeypatch):
    from app.models.capture_analysis import CaptureAnalysis

    sessions = _daily_delta_retry_store(tmp_path, monkeypatch)
    monkeypatch.setattr(
        worker,
        "_reconcile_daily_capture_delta",
        lambda **kwargs: {"status": "not_applicable", "run_id": None, "error_code": None},
    )

    assert asyncio.run(worker.process_one_qwen_job()) is True
    db = sessions()
    completed = db.query(QwenBackgroundJob).filter_by(id="daily-delta-job").one()
    assert completed.status == "completed"
    assert completed.attempt_count == 2
    result = json.loads(completed.result_text)
    assert result["delta_reconciliation_status"] == "not_applicable"
    assert result["delta_engine_output_persisted"] is False
    assert result["longitudinal_processing_status"] == "completed"
    analysis = db.query(CaptureAnalysis).filter_by(id="daily-delta-analysis").one()
    assert analysis.execution_status["delta_reconciliation"] == "not_applicable"
    assert analysis.execution_status["longitudinal_processing"] == "completed"
    db.close()
