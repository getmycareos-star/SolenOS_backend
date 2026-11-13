"""Database-backed Qwen job worker.

Jobs are durable and read-only. The worker classifies the task internally,
then delegates to the same authenticated, grounded assistant implementation.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import inspect, or_

from app.core.database import SessionLocal
from app.models.qwen_background_job import QwenBackgroundJob, QwenModelOutput, QwenJobEvent, QwenSafetyEvaluation
from app.services.qwen_gateway import qwen_chat, extract_review_candidates
from app.services.qwen_workflows import ENABLED_WORKFLOWS

logger = logging.getLogger(__name__)


def _process_document_evidence(evidence_id: str, person_id: str, caregiver_id: str) -> tuple[dict, dict]:
    """Run OCR and document understanding in a worker thread with its own DB session."""
    from types import SimpleNamespace
    from app.api.document_lifecycle import extract_saved_document
    from app.api.document_intelligence import analyze_saved_document

    scoped_request = SimpleNamespace(
        state=SimpleNamespace(
            scoped_person_id=person_id,
            scoped_caregiver_id=caregiver_id,
        )
    )
    session = SessionLocal()
    try:
        extraction = extract_saved_document(evidence_id, scoped_request, session)
        # Do not ask the interpretation model to read an incomplete document.
        # The durable worker retries only transiently failed pages; unreadable
        # and blocked pages remain explicitly partial with no clinical candidates.
        if extraction.get("extraction_status") == "partial":
            return extraction, {
                "analysis_id": extraction.get("analysis_id"),
                "intelligence_status": "partial",
                "observations": [],
                "unprocessed_page_numbers": extraction.get("unprocessed_page_numbers") or [],
                "retryable_page_numbers": extraction.get("retryable_page_numbers") or [],
                "retry_exhausted_page_numbers": extraction.get("retry_exhausted_page_numbers") or [],
                "processed_page_count": max(
                    0,
                    len(extraction.get("processed_page_numbers") or [])
                    - len(extraction.get("unreadable_page_numbers") or []),
                ),
                "total_page_count": extraction.get("page_count"),
            }
        intelligence = analyze_saved_document(evidence_id, scoped_request, session)
        return extraction, intelligence
    finally:
        session.close()
# High-confidence local routing rules take precedence over the small model's
# classification. This avoids sending an obvious medication/appointment/security
# request to the wrong contract. Ambiguous requests still use constrained Qwen
# classification and fail closed if the returned workflow is invalid.
ROUTE_PHRASES = {
    "daily_capture": ("today's caregiver-reported observation", "daily capture", "record today's observation"),
    "add_record": ("care record correction", "add a record", "add record", "draft a care record correction", "proposed care-record entry"),
    "document": ("document text", "uploaded document", "document or extracted text", "explain the document"),
    "living_care_record": ("living care record", "current lcr"),
    "care_history_search": ("search care history", "care history", "earlier fall", "previous fall", "historical information"),
    "care_situation_change": ("compare current and prior care situation", "compare current and prior observation", "care situation change", "what has changed in the care situation", "what has changed between current and previous care situation", "what has changed between the current and previous care situation", "compare the current care situation with the previous one", "changes between current and previous care notes", "compare current and prior observations", "what changed since the last observation", "difference between current and previous care situation"),
    "delta_engine": ("delta engine", "reconcile changes", "possible changes or contradictions against existing care evidence", "review candidates against evidence"),
    "evidence_provenance": ("evidence provenance", "source and provenance", "provenance of the claim", "identify the source of the claim"),
    "uncertainty_contradictions": ("uncertainty or contradictions", "uncertainty and contradictions", "conflicting information", "ambiguous information"),
    "returning_caregiver": ("returning caregiver", "returning-caregiver", "orient me as a returning"),
    "after_appointment": ("after-appointment", "after appointment", "post-appointment", "after-appointment notes", "clinician notes"),
    "appointment": ("prepare appointment questions", "appointment questions", "available appointment details", "appointment details and unanswered questions"),
    "handoff": ("care handoff", "draft a handoff", "care handoff"),
    "professional_summary": ("professional-facing care summary", "professional summary", "summary for a clinician"),
    "open_loops": ("open loops", "open follow-ups", "unresolved questions and follow-ups", "pending follow-ups"),
    "two_week_review": ("two-week review", "two week review", "past fourteen days", "last fourteen days", "last 14 days"),
    "decision_outcome": ("decision and outcome", "decision from its outcome", "care decision and its outcome", "decision/outcome", "what was decided and what happened afterward", "track the outcome of a care decision", "compare the decision with its outcome", "what happened after the care decision", "decision versus outcome"),
    "medication_day": ("medication-day observations", "medication day", "medication observations", "medication-related observations", "medication log", "medication taken today", "medication routine", "what happened on medication day", "today’s medication observations"),
    "reminders_notifications": ("reminders and notifications", "review my reminders", "reminder status", "notification status"),
    "care_coordination": ("care responsibilities and coordination", "care coordination", "coordinate care responsibilities", "coordination and delegation", "who is responsible for each care task", "coordinate responsibilities between caregivers", "divide care responsibilities", "care tasks and responsible people", "delegating care tasks", "assign responsibilities among caregivers"),
    "companion": ("i feel overwhelmed", "i’m overwhelmed", "i'm overwhelmed", "i am overwhelmed", "help me work out the next practical step", "help me think through this", "i need someone to talk to", "i feel stressed about caregiving", "i need support with caregiving", "talk through what is happening", "help me decide what to do next"),
    "caregiver_feedback": ("phrase a correction to a care record", "caregiver feedback", "feedback or a correction", "help me phrase feedback", "suggest a correction to this care note", "help me give feedback on a care record", "word a correction for a care entry", "how can i correct this care note", "feedback about a care record", "help me phrase a care-record correction"),
    "sharing_export": ("shareable care summary", "sharing and export", "prepare a shareable", "export a care summary"),
    "roles_access": ("who has access to this care record", "who can view this record", "which caregivers can access", "access permissions for the record", "who is allowed to see this care information", "care record access permissions", "roles and access", "role or permission state", "permissions for this care record"),
    "account_recovery": ("account recovery", "recover my account", "safe account recovery steps", "reset my password", "i cannot log in", "can’t access my account", "can't access my account", "cannot access my account", "forgot my password", "lost access to my account", "recover login access", "account password reset"),
    "settings_security": ("privacy and security settings", "security settings", "settings security", "privacy settings", "change my privacy options", "manage privacy controls", "account security options", "configure security settings", "privacy controls", "manage account security"),
    "record_lifecycle": ("archiving or restoring a care record", "archive or restore", "pin, archive, restore", "record lifecycle", "restore an archived record", "archive this record", "unarchive a record", "pin this record", "delete or archive a record", "record archive status"),
    "offline_sync": ("offline changes have synced", "offline and sync", "offline sync", "synchronization status"),
    "ai_reliability": ("qwen availability or retry state", "qwen availability", "qwen reliability", "model availability and retry", "is qwen available", "qwen is not responding", "assistant availability", "why did the ai request fail", "retry an ai request", "check model status", "assistant reliability status"),
    "audit_review": ("audit and review candidates", "audit trail", "audit review", "audit entries", "review the audit trail", "show audit history", "inspect audit records", "review recorded actions", "audit log entries", "review the audit log"),
    "care_transition": ("care transition", "hospital discharge", "discharge from hospital", "discharge summary", "transition from hospital to home", "moving from hospital to home", "rehabilitation to home", "transition of care", "discharge reconciliation", "after discharge", "leaving hospital", "handoff after discharge", "transition documents"),
    "incident_follow_through": ("incident follow-through", "incident follow through", "follow up after a fall", "follow-up after a fall", "what happened after the fall", "follow up on the wandering episode", "follow-up on the wandering episode", "track the outcome of an incident", "unresolved incident follow-up", "missed visit follow-up", "follow up after an incident", "follow-up after an incident", "incident outcome"),
    "care_coverage_gap": ("care coverage gap", "coverage gap", "gap in care coverage", "caregiver coverage gap", "who is covering care", "uncovered care period", "coverage for the care visit", "missing caregiver coverage", "care schedule coverage", "check for care coverage gaps"),
    "care_instruction_change_tracker": ("care instruction change tracker", "care instruction change", "change in care instructions", "updated care instructions", "compare old and new care instructions", "compare previous and current care instructions", "conflicting care instructions", "instruction effective date", "track a care instruction change", "review changed care instructions", "new care instructions"),
    "critical_information_completeness": ("critical information completeness", "check critical care information is complete", "missing critical care information", "care record completeness", "check emergency information completeness", "what critical information is missing", "review missing care information", "critical information checklist", "check the care record for missing essentials", "care information completeness review"),
    "personal_preferences_distress_context": ("personal preferences and distress context", "personal preferences", "distress context", "known distress triggers", "what helps when they are distressed", "what may cause distress", "care preferences", "preferred calming approaches", "known triggers and preferences", "documented comfort preferences", "review personal care preferences"),
    "caregiver_strain_support": ("caregiver strain and support", "caregiver strain", "caregiver stress", "caregiver support needs", "caregiver is overwhelmed", "caregiver burnout", "support for the caregiver", "caregiver respite needs", "caregiver workload concerns", "review caregiver support", "caregiving strain"),
    "document_validity_renewal": ("document validity and renewal", "document validity", "document expiration date", "document expiry", "renewal date", "document renewal", "check document expiry", "review expiring care documents", "when does this document expire", "validity of care documents", "renewal deadline in the document"),
    "emergency_readiness": ("emergency readiness", "emergency preparedness", "emergency plan", "documented emergency plan", "review the documented emergency plan", "care emergency plan", "what to prepare for an emergency", "emergency contact information", "emergency information in the care record", "review the emergency plan", "emergency handoff information", "what information is missing for an emergency"),
}


def _route_from_text(task_text: str, context_text: str = "") -> str | None:
    combined = f"{task_text}\\n{context_text}".casefold().replace("–", "-").replace("—", "-")
    matches: list[tuple[int, str]] = []
    for workflow, phrases in ROUTE_PHRASES.items():
        for phrase in phrases:
            if phrase in combined:
                matches.append((len(phrase), workflow))
    if not matches:
        return None
    longest = max(length for length, _ in matches)
    winners = {workflow for length, workflow in matches if length == longest}
    return next(iter(winners)) if len(winners) == 1 else None


def _requests_disabled_community(task_text: str) -> bool:
    lowered = task_text.casefold()
    return any(phrase in lowered for phrase in (
        "community", "peer stories", "caregiver stories",
        "other caregivers' experiences", "other caregivers’ experiences",
    ))


async def _classify_task(task_text: str, context_text: str) -> str:
    if _requests_disabled_community(task_text):
        raise ValueError("community_disabled")
    routed = _route_from_text(task_text)
    if routed is not None:
        return routed

    choices = ", ".join(ENABLED_WORKFLOWS)
    prompt = (
        "Classify the caregiver's request into exactly one enabled SolenOS workflow ID. "
        "Community is disabled and is not an option. Do not answer the caregiver. "
        "Return only a JSON object with one key workflow and a value copied exactly from this list: "
        + choices
    )
    # Ollama supports constrained JSON output. Use a schema so the model cannot
    # choose a disabled workflow or return free-form prose for this routing step.
    schema = {
        "type": "object",
        "properties": {"workflow": {"type": "string", "enum": list(ENABLED_WORKFLOWS)}},
        "required": ["workflow"],
        "additionalProperties": False,
    }
    raw = await asyncio.to_thread(
        qwen_chat,
        [{"role": "system", "content": prompt},
         {"role": "user", "content": json.dumps({"request": task_text[:1800], "screen_context": context_text[:3000]})}],
        response_format=schema,
        options={"temperature": 0, "num_predict": 48},
    )
    try:
        parsed = json.loads(raw.strip())
        value = parsed.get("workflow") if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, AttributeError, TypeError):
        value = None
    if not isinstance(value, str) or value not in ENABLED_WORKFLOWS:
        # Do not guess or silently choose a workflow if model routing is malformed.
        raise ValueError("workflow_classification_invalid")
    return value


async def _process_daily_capture_extraction(db, job: QwenBackgroundJob) -> bool:
    """Extract a saved Daily Care report after the caregiver-facing save has committed."""
    from app.models.care import CareEvent, Evidence
    from app.models.capture_analysis import CaptureAnalysis
    from app.services.capture_review import persist_source_grounded_daily_observations
    from app.services.uncertainty import persist_source_clarification_questions
    from app.services.care_continuity_execution import execute_care_continuity_reconciliation
    from app.services.lcr_context import build_lcr_context

    try:
        event_ids = json.loads(job.source_event_ids_json or "[]")
        evidence_ids = json.loads(job.source_evidence_ids_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("daily_capture_source_references_invalid") from exc
    if (
        not isinstance(event_ids, list) or len(event_ids) != 1
        or not isinstance(evidence_ids, list) or len(evidence_ids) != 1
        or not all(isinstance(value, str) and value.strip() for value in event_ids + evidence_ids)
    ):
        raise ValueError("daily_capture_source_scope_invalid")

    event_id = event_ids[0]
    evidence_id = evidence_ids[0]
    event = db.query(CareEvent).filter(
        CareEvent.id == event_id,
        CareEvent.person_id == job.person_id,
    ).first()
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == job.person_id,
    ).first()
    if event is None or evidence is None:
        raise ValueError("daily_capture_source_missing")
    if str(event.created_by_caregiver_id) != str(job.caregiver_id):
        raise ValueError("daily_capture_event_caregiver_scope_invalid")
    if str(evidence.uploaded_by_caregiver_id) != str(job.caregiver_id):
        raise ValueError("daily_capture_evidence_caregiver_scope_invalid")
    if evidence_id not in {str(value) for value in (event.evidence_ids or [])}:
        raise ValueError("daily_capture_evidence_link_invalid")
    if getattr(evidence, "evidence_status", None) == "deleted_by_caregiver":
        raise ValueError("daily_capture_source_deleted")

    try:
        evidence_payload = json.loads(evidence.source_text or "{}")
    except (TypeError, ValueError):
        evidence_payload = {}
    try:
        job_context = json.loads(job.context_text or "{}")
    except (TypeError, ValueError):
        job_context = {}
    capture_mode = (
        job_context.get("capture_mode")
        if isinstance(job_context, dict) else None
    )
    natural_language_update = capture_mode == "natural_language_update"
    original_capture = evidence_payload.get("original_capture") if isinstance(evidence_payload, dict) else None
    if isinstance(original_capture, dict):
        source_text = original_capture.get("notes") if natural_language_update else json.dumps(
            original_capture, ensure_ascii=False, sort_keys=True, default=str
        )
    else:
        source_text = evidence_payload.get("original_notes") if isinstance(evidence_payload, dict) else None
        if not isinstance(source_text, str) or not source_text.strip():
            quick_check_in = evidence_payload.get("quick_check_in") if isinstance(evidence_payload, dict) else None
            source_text = json.dumps(quick_check_in, ensure_ascii=False, sort_keys=True, default=str) if isinstance(quick_check_in, dict) else None
    if not isinstance(source_text, str) or not source_text.strip():
        try:
            event_detail = json.loads(event.description or "{}")
        except (TypeError, ValueError):
            event_detail = {}
        if isinstance(event_detail, dict):
            source_text = event_detail.get("notes") or json.dumps(
                event_detail, ensure_ascii=False, sort_keys=True, default=str
            )
    if not isinstance(source_text, str) or not source_text.strip():
        raise ValueError("daily_capture_source_text_missing")
    source_text = source_text[:12_000]

    analysis = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.person_id == job.person_id,
        CaptureAnalysis.caregiver_id == job.caregiver_id,
        CaptureAnalysis.source_event_id == event_id,
        CaptureAnalysis.source_evidence_id == evidence_id,
    ).order_by(CaptureAnalysis.created_at.desc()).first()
    if analysis is None:
        analysis = CaptureAnalysis(
            person_id=job.person_id,
            caregiver_id=job.caregiver_id,
            source_event_id=event_id,
            source_evidence_id=evidence_id,
            source_text=source_text,
            extraction_status="processing",
            candidates={"observations": [], "open_questions": []},
            review_history=[],
            execution_status={
                "source_preservation": "verified",
                "evidence": "verified",
                "extraction": "processing",
                "assessment": "pending",
                "review": "not_required",
                "reconciliation": "pending",
                "longitudinal_retrieval": "not_verified",
            },
        )
        db.add(analysis)
    else:
        analysis.source_text = source_text
        analysis.extraction_status = "processing"
        analysis.execution_status = {
            **(analysis.execution_status or {}),
            "source_preservation": "verified",
            "evidence": "verified",
            "extraction": "processing",
            "assessment": "pending",
            "review": "not_required",
            "reconciliation": "pending",
            "longitudinal_retrieval": "not_verified",
        }
    db.commit()
    db.refresh(analysis)

    candidates = await asyncio.to_thread(
        extract_review_candidates,
        source_text,
        source_event_id=event_id,
        source_evidence_id=evidence_id,
    )
    if not isinstance(candidates, dict) or not isinstance(candidates.get("observations"), list):
        raise ValueError("daily_capture_extraction_invalid")
    candidates = dict(candidates)
    candidates["observations"] = [
        {
            **item,
            "review_status": (
                "recorded_as_reported_observation"
                if natural_language_update else "pending_review"
            ),
        }
        for item in candidates["observations"]
        if isinstance(item, dict)
    ]
    if natural_language_update:
        # A free-form update preserves the caregiver's report as reported
        # evidence; model-generated questions must not create extra work.
        candidates["open_questions"] = []
    elif not isinstance(candidates.get("open_questions"), list):
        candidates["open_questions"] = []

    current_job = db.query(QwenBackgroundJob).filter(
        QwenBackgroundJob.id == job.id,
        QwenBackgroundJob.person_id == job.person_id,
        QwenBackgroundJob.caregiver_id == job.caregiver_id,
    ).with_for_update().first()
    if current_job is None or current_job.status == "cancelled":
        db.rollback()
        return True

    event = db.query(CareEvent).filter(
        CareEvent.id == event_id,
        CareEvent.person_id == job.person_id,
        CareEvent.created_by_caregiver_id == job.caregiver_id,
    ).first()
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == job.person_id,
        Evidence.uploaded_by_caregiver_id == job.caregiver_id,
    ).first()
    if event is None or evidence is None or getattr(evidence, "evidence_status", None) == "deleted_by_caregiver":
        current_job.status = "cancelled"
        current_job.error_code = "daily_capture_source_deleted"
        current_job.locked_at = None
        db.add(QwenJobEvent(
            job_id=current_job.id,
            person_id=current_job.person_id,
            caregiver_id=current_job.caregiver_id,
            event_type="daily_capture_source_cancelled",
            from_status="processing",
            to_status="cancelled",
            error_code=current_job.error_code,
            metadata_json={"source_event_id": event_id, "source_evidence_id": evidence_id},
        ))
        db.commit()
        return True

    candidate_count = len(candidates["observations"])
    if natural_language_update:
        observation_count = persist_source_grounded_daily_observations(
            db,
            person_id=job.person_id,
            caregiver_id=job.caregiver_id,
            evidence_id=evidence_id,
            source_event_id=event_id,
            source_text=source_text,
            candidates=candidates,
            reported_at=event.recorded_at,
        )
    else:
        # Structured captures are not promoted from model candidates into LCR
        # assertions. Keep every extracted item pending caregiver review.
        observation_count = 0
        questions = candidates.get("open_questions") or []
        if questions:
            persist_source_clarification_questions(
                db,
                person_id=job.person_id,
                caregiver_id=job.caregiver_id,
                source_kind="daily_capture_extraction",
                source_record_type="care_event",
                source_record_id=event_id,
                evidence_ids=[evidence_id],
                questions=questions,
            )
    assessment_status = "available" if candidate_count else "not_applicable"
    review_status = "not_required" if natural_language_update or not candidate_count else "required"
    reconciliation = execute_care_continuity_reconciliation(
        db,
        job.person_id,
        workflow="daily_care_entry",
        source_caregiver_id=job.caregiver_id,
        source_event_id=event_id,
        source_evidence_id=evidence_id,
        reconcile_lcr=True,
        stage_statuses={
            "extraction": "available",
            "assessment": assessment_status,
            "review": review_status,
        },
    )
    lcr_context = build_lcr_context(
        db,
        job.person_id,
        caregiver_id=job.caregiver_id,
        limit_per_source=10,
    )
    projection_status = (
        "verified"
        if any(str(row.get("id")) == str(event_id) for row in lcr_context.get("events", []))
        else "not_verified"
    )
    if projection_status != "verified":
        raise RuntimeError("daily_capture_lcr_projection_not_verified")

    analysis = db.query(CaptureAnalysis).filter(
        CaptureAnalysis.id == analysis.id,
        CaptureAnalysis.person_id == job.person_id,
        CaptureAnalysis.caregiver_id == job.caregiver_id,
    ).with_for_update().first()
    if analysis is None:
        raise RuntimeError("daily_capture_analysis_disappeared")
    analysis.candidates = candidates
    analysis.extraction_status = "available"
    analysis.execution_status = {
        "source_preservation": "verified",
        "evidence": "verified",
        "extraction": "available",
        "assessment": assessment_status,
        "review": review_status,
        "reconciliation": "completed",
        "longitudinal_retrieval": "not_verified",
        "projection": projection_status,
        "reconciliation_run_id": reconciliation.get("run_id"),
    }
    current_job.status = "completed"
    current_job.result_text = json.dumps({
        "analysis_id": analysis.id,
        "source_event_id": event_id,
        "source_evidence_id": evidence_id,
        "extraction_status": "available",
        "candidate_count": candidate_count,
        "observation_count": observation_count,
        "review_required": review_status == "required",
        "capture_mode": capture_mode or "legacy_natural_language_update",
        "assessment_status": assessment_status,
        "reconciliation_status": "completed",
        "projection_status": projection_status,
        "model_output_written_to_lcr": False,
        "source_grounded_observations_projected": observation_count,
    }, ensure_ascii=False, sort_keys=True)
    current_job.completed_at = datetime.now(timezone.utc)
    current_job.locked_at = None
    current_job.error_code = None
    db.add(QwenJobEvent(
        job_id=current_job.id,
        person_id=current_job.person_id,
        caregiver_id=current_job.caregiver_id,
        event_type="daily_capture_extraction_completed",
        from_status="processing",
        to_status="completed",
        metadata_json={
            "source_event_id": event_id,
            "source_evidence_id": evidence_id,
            "analysis_id": analysis.id,
            "candidate_count": candidate_count,
            "observation_count": observation_count,
            "review_required": review_status == "required",
            "capture_mode": capture_mode or "legacy_natural_language_update",
            "projection_status": projection_status,
            "model_output_written_to_lcr": False,
            "source_grounded_observations_projected": observation_count,
        },
    ))
    db.commit()
    return True


async def process_one_qwen_job(job_id: str | None = None) -> bool:
    db = SessionLocal()
    job = None
    try:
        # The API container can start while the deployment migration command is
        # still creating the queue table. Avoid querying it before its DDL commits;
        # the poll loop retries after its normal idle wait.
        schema = inspect(db.get_bind())
        if not schema.has_table("qwen_background_jobs") or not schema.has_table("qwen_job_events"):
            return False

        now = datetime.now(timezone.utc)
        # Recover a job left processing by a crashed worker after a 10-minute lease.
        stale_before = now - timedelta(minutes=10)
        job = (
            db.query(QwenBackgroundJob)
            .filter(
                or_(
                    QwenBackgroundJob.status == "queued",
                    (QwenBackgroundJob.status == "processing") & (QwenBackgroundJob.locked_at < stale_before),
                ),
                QwenBackgroundJob.available_at <= now,
                *([QwenBackgroundJob.id == job_id] if job_id else []),
            )
            .order_by(QwenBackgroundJob.available_at.asc(), QwenBackgroundJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if job is None:
            return False
        previous_status = job.status
        job.status = "processing"
        job.locked_at = now
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.error_code = None
        db.add(QwenJobEvent(job_id=job.id, person_id=job.person_id, caregiver_id=job.caregiver_id, event_type="job_claimed", from_status=previous_status, to_status="processing", metadata_json={"attempt": job.attempt_count}))
        db.commit()
        db.refresh(job)

        if job.workflow == "daily_capture_extraction":
            return await _process_daily_capture_extraction(db, job)

        # Document intake reuses this durable PostgreSQL queue so extraction and
        # review continue even if the caregiver closes the app after upload.
        # It creates source-bound CaptureAnalysis candidates only; it never
        # creates LCR assertions or a QwenModelOutput.
        if job.workflow == "document_intake":
            evidence_ids = json.loads(job.evidence_ids_json or "[]")
            if not isinstance(evidence_ids, list) or not evidence_ids or len(evidence_ids) > 10:
                raise ValueError("document_intake_evidence_scope_invalid")
            completed = []
            for evidence_id in evidence_ids:
                if not isinstance(evidence_id, str) or not evidence_id.strip():
                    raise ValueError("document_intake_evidence_id_invalid")
                current_job = db.query(QwenBackgroundJob).filter(
                    QwenBackgroundJob.id == job.id
                ).with_for_update().first()
                if current_job is None or current_job.status == "cancelled":
                    db.rollback()
                    return True
                extraction, intelligence = await asyncio.to_thread(
                    _process_document_evidence,
                    evidence_id,
                    job.person_id,
                    job.caregiver_id,
                )
                # A caregiver may delete the source while OCR/model work is in
                # flight. Never let this stale worker overwrite cancellation or
                # requeue the deleted document after the processing call returns.
                current_job = db.query(QwenBackgroundJob).filter(
                    QwenBackgroundJob.id == job.id
                ).with_for_update().first()
                if current_job is None or current_job.status == "cancelled":
                    db.rollback()
                    return True
                job = current_job
                completed.append({
                    "evidence_id": evidence_id,
                    "analysis_id": extraction.get("analysis_id"),
                    "intelligence_status": intelligence.get("intelligence_status"),
                    "candidate_count": len(intelligence.get("observations") or []),
                    "processed_page_count": intelligence.get("processed_page_count"),
                    "total_page_count": intelligence.get("total_page_count"),
                    "unprocessed_page_numbers": intelligence.get("unprocessed_page_numbers") or [],
                })
                pending_pages = intelligence.get("unprocessed_page_numbers") or []
                retryable_pages = extraction.get("retryable_page_numbers") or []
                retry_exhausted_pages = extraction.get("retry_exhausted_page_numbers") or []
                if retryable_pages:
                    # Retry only transient OCR failures, with increasing delay.
                    # Page attempts are persisted with the extraction, so a
                    # worker restart cannot reset the retry budget.
                    page_attempts = extraction.get("page_retry_attempts") or {}
                    latest_page_attempt = max(
                        [int(page_attempts.get(str(page)) or 1) for page in retryable_pages],
                        default=1,
                    )
                    retry_delays = [3, 30, 300, 1800, 7200, 28800, 86400]
                    delay_seconds = retry_delays[min(max(latest_page_attempt - 1, 0), len(retry_delays) - 1)]
                    job.status = "queued"
                    job.attempt_count = 0
                    job.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                    job.locked_at = None
                    job.error_code = None
                    job.result_text = json.dumps(completed)
                    db.add(QwenJobEvent(
                        job_id=job.id,
                        person_id=job.person_id,
                        caregiver_id=job.caregiver_id,
                        event_type="document_intake_page_retry_scheduled",
                        from_status="processing",
                        to_status="queued",
                        metadata_json={
                            "source_evidence_id": evidence_id,
                            "retryable_page_numbers": retryable_pages,
                            "retry_exhausted_page_numbers": retry_exhausted_pages,
                            "page_retry_attempts": page_attempts,
                            "retry_delay_seconds": delay_seconds,
                            "unprocessed_page_numbers": pending_pages,
                            "written_to_lcr": False,
                        },
                    ))
                    db.commit()
                    return True
                exhausted_page_keys = {str(page) for page in retry_exhausted_pages}
                remaining_window_pages = [
                    page for page in pending_pages
                    if str(page) not in exhausted_page_keys
                ]
                if pending_pages and not retryable_pages and remaining_window_pages:
                    # A bounded page window is unfinished work, not a completed
                    # document and not an OCR failure. Keep processing later
                    # windows even when an earlier page has exhausted its retry
                    # budget; the document remains incomplete until every
                    # other page has been processed.
                    job.status = "queued"
                    job.attempt_count = 0
                    job.available_at = datetime.now(timezone.utc)
                    job.locked_at = None
                    job.error_code = None
                    job.result_text = json.dumps(completed)
                    db.add(QwenJobEvent(
                        job_id=job.id,
                        person_id=job.person_id,
                        caregiver_id=job.caregiver_id,
                        event_type="document_intake_page_window_completed",
                        from_status="processing",
                        to_status="queued",
                        metadata_json={
                            "source_evidence_id": evidence_id,
                            "processed_page_count": intelligence.get("processed_page_count"),
                            "total_page_count": intelligence.get("total_page_count"),
                            "unprocessed_page_numbers": pending_pages,
                            "retry_exhausted_page_numbers": retry_exhausted_pages,
                            "remaining_window_page_numbers": remaining_window_pages,
                            "written_to_lcr": False,
                        },
                    ))
                    db.commit()
                    return True
                if retry_exhausted_pages:
                    job.status = "failed"
                    job.error_code = "document_page_retry_exhausted"
                    job.locked_at = None
                    job.result_text = json.dumps(completed)
                    db.add(QwenJobEvent(
                        job_id=job.id,
                        person_id=job.person_id,
                        caregiver_id=job.caregiver_id,
                        event_type="document_intake_page_retry_exhausted",
                        from_status="processing",
                        to_status="failed",
                        error_code=job.error_code,
                        metadata_json={
                            "source_evidence_id": evidence_id,
                            "retry_exhausted_page_numbers": retry_exhausted_pages,
                            "unprocessed_page_numbers": pending_pages,
                            "written_to_lcr": False,
                        },
                    ))
                    db.commit()
                    return True
            job.status = "completed"
            job.workflow = "document_intake"
            job.result_text = json.dumps(completed)
            job.completed_at = datetime.now(timezone.utc)
            job.locked_at = None
            job.error_code = None
            db.add(QwenJobEvent(
                job_id=job.id,
                person_id=job.person_id,
                caregiver_id=job.caregiver_id,
                event_type="document_intake_completed",
                from_status="processing",
                to_status="completed",
                metadata_json={
                    "source_evidence_ids": evidence_ids,
                    "analysis_count": len(completed),
                    "review_required": True,
                    "written_to_lcr": False,
                },
            ))
            db.commit()
            return True

        workflow = await _classify_task(job.task_text, job.context_text or "")
        job.workflow = workflow
        db.commit()

        # Import only after app initialization to avoid a router/worker import cycle.
        from app.api.collaboration import QwenAssistRequest, qwen_support_assist

        text = job.task_text.strip()
        if (job.context_text or "").strip():
            text += "\n\nContext supplied with this request (may be an unsaved draft; do not claim it is saved):\n" + job.context_text.strip()[:3000]
        payload = QwenAssistRequest(
            workflow=workflow,
            text=text[:7900],
            evidence_ids=json.loads(job.evidence_ids_json or "[]"),
            appointment_id=job.appointment_id,
        )
        fake_request = SimpleNamespace(
            state=SimpleNamespace(scoped_person_id=job.person_id, scoped_caregiver_id=job.caregiver_id)
        )
        result = await qwen_support_assist(payload, fake_request, db)
        # The source may have been deleted while inference was running. A
        # cancelled job must never persist a derived answer after that deletion.
        current_job = db.query(QwenBackgroundJob).filter(
            QwenBackgroundJob.id == job.id
        ).with_for_update().first()
        if current_job is None or current_job.status == "cancelled":
            db.rollback()
            return True
        job = current_job
        if result.get("ai_generated") is not True or result.get("provider_status") != "available":
            raise RuntimeError("Qwen background worker received a response without confirmed live inference")
        model_name = str(result.get("model") or os.getenv("OLLAMA_MODEL") or "").strip()
        if not model_name:
            raise RuntimeError("Qwen background worker cannot persist an output without model identity")
        output = QwenModelOutput(
            job_id=job.id, person_id=job.person_id, caregiver_id=job.caregiver_id,
            workflow=result["workflow"], model_name=model_name, output_text=result["reply"],
            output_metadata={
                "inference_provider": "ollama",
                "model_name": model_name,
                "live_inference_confirmed": True,
                "persistence": "read_only_assistance",
                "written_to_lcr": False,
                "source_provenance_version": "qwen-source-records-v1",
                "source_document_analysis_ids": result.get("source_document_analysis_ids", []),
                "source_appointment_ids": result.get("source_appointment_ids", []),
                "source_handoff_ids": result.get("source_handoff_ids", []),
                "source_notification_ids": result.get("source_notification_ids", []),
                "source_freshness_assessment_ids": result.get("source_freshness_assessment_ids", []),
                "source_freshness_status": result.get("source_freshness_status", "not_applicable"),
                "claim_provenance": result.get("claim_provenance", {}),
            },
            source_event_ids_json=json.dumps(result.get("source_event_ids", [])),
            source_evidence_ids_json=json.dumps(result.get("source_evidence_ids", [])),
            review_status="pending_review", review_required=True, written_to_lcr=False,
        )
        db.add(output)
        db.flush()
        db.add(QwenSafetyEvaluation(
            output_id=output.id, job_id=job.id, person_id=job.person_id,
            caregiver_id=job.caregiver_id, risk_class="unassessed",
            decision="review_required", human_review_required=True,
            violations_json=[], policy_references_json=[],
        ))
        job.result_text = None
        job.workflow = result["workflow"]
        job.source_event_ids_json = json.dumps(result.get("source_event_ids", []))
        job.source_evidence_ids_json = json.dumps(result.get("source_evidence_ids", []))
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.locked_at = None
        job.error_code = None
        db.add(QwenJobEvent(job_id=job.id, person_id=job.person_id, caregiver_id=job.caregiver_id, event_type="job_completed", from_status="processing", to_status="completed", metadata_json={"output_id": output.id, "review_required": True, "written_to_lcr": False}))
        db.commit()
        return True
    except Exception as exc:
        logger.warning("Qwen background job failed (%s)", type(exc).__name__)
        if job is not None:
            try:
                db.rollback()
                current = db.query(QwenBackgroundJob).filter(QwenBackgroundJob.id == job.id).first()
                if current is not None and current.status == "cancelled" and current.error_code == "document_deleted":
                    # The caregiver's deletion is terminal. Do not turn it into
                    # a retry merely because an already-running worker returned.
                    db.rollback()
                    return True
                if current is not None:
                    previous_status = current.status
                    current.locked_at = None
                    current.error_code = exc.detail[:48] if isinstance(exc, HTTPException) else (
                        str(exc)[:48] if isinstance(exc, ValueError) else "qwen_processing_failed"
                    )
                    permanent_http_error = (
                        isinstance(exc, HTTPException)
                        and 400 <= int(exc.status_code) < 500
                        and int(exc.status_code) != 429
                    )
                    if (
                        isinstance(exc, ValueError) and str(exc) == "community_disabled"
                    ) or permanent_http_error:
                        current.status = "failed"
                    elif current.attempt_count < 3:
                        current.status = "queued"
                        current.available_at = datetime.now(timezone.utc) + timedelta(seconds=15 * current.attempt_count)
                    else:
                        current.status = "failed"
                    if current.workflow == "daily_capture_extraction":
                        from app.models.capture_analysis import CaptureAnalysis
                        try:
                            source_event_ids = json.loads(current.source_event_ids_json or "[]")
                        except (TypeError, ValueError, json.JSONDecodeError):
                            source_event_ids = []
                        analysis = db.query(CaptureAnalysis).filter(
                            CaptureAnalysis.person_id == current.person_id,
                            CaptureAnalysis.caregiver_id == current.caregiver_id,
                            CaptureAnalysis.source_event_id.in_(source_event_ids or ["__none__"]),
                        ).order_by(CaptureAnalysis.created_at.desc()).first()
                        if analysis is not None:
                            terminal_failure = current.status == "failed"
                            analysis.extraction_status = "failed" if terminal_failure else "queued"
                            analysis.execution_status = {
                                **(analysis.execution_status or {}),
                                "extraction": "failed" if terminal_failure else "queued",
                                "assessment": "failed" if terminal_failure else "pending",
                                "reconciliation": "not_completed" if terminal_failure else "pending",
                                "projection": "not_verified",
                            }
                    db.add(QwenJobEvent(job_id=current.id, person_id=current.person_id, caregiver_id=current.caregiver_id, event_type="job_failed_or_requeued", from_status=previous_status, to_status=current.status, error_code=current.error_code, metadata_json={"attempt": current.attempt_count}))
                    db.commit()
            except Exception:
                db.rollback()
        # A failed poll before claiming a job is idle, so the outer loop
        # backs off. A claimed job that was safely requeued/failed is work handled.
        return job is not None
    finally:
        db.close()


async def qwen_worker_loop() -> None:
    """Single in-process poller; DB row locking protects against duplicate claims."""
    while True:
        try:
            worked = await process_one_qwen_job()
            if not worked:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Qwen worker loop error")
            await asyncio.sleep(5)
