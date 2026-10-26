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
from sqlalchemy import or_

from app.core.database import SessionLocal
from app.models.qwen_background_job import QwenBackgroundJob
from app.services.qwen_gateway import qwen_chat

logger = logging.getLogger(__name__)

ENABLED_WORKFLOWS = (
    "daily_capture", "add_record", "document", "living_care_record",
    "care_history_search", "care_situation_change", "delta_engine",
    "evidence_provenance", "uncertainty_contradictions", "returning_caregiver",
    "appointment", "after_appointment", "handoff", "professional_summary",
    "open_loops", "two_week_review", "decision_outcome", "medication_day",
    "reminders_notifications", "care_coordination", "companion",
    "caregiver_feedback", "sharing_export", "roles_access", "account_recovery",
    "settings_security", "record_lifecycle", "offline_sync", "ai_reliability",
    "audit_review", "care_transition", "incident_follow_through", "emergency_readiness",
)


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


async def process_one_qwen_job() -> bool:
    db = SessionLocal()
    job = None
    try:
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
            )
            .order_by(QwenBackgroundJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if job is None:
            return False
        job.status = "processing"
        job.locked_at = now
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.error_code = None
        db.commit()
        db.refresh(job)

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
        job.result_text = result["reply"]
        job.workflow = result["workflow"]
        job.source_event_ids_json = json.dumps(result.get("source_event_ids", []))
        job.source_evidence_ids_json = json.dumps(result.get("source_evidence_ids", []))
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.locked_at = None
        job.error_code = None
        db.commit()
        return True
    except Exception as exc:
        logger.warning("Qwen background job failed (%s)", type(exc).__name__)
        if job is not None:
            try:
                db.rollback()
                current = db.query(QwenBackgroundJob).filter(QwenBackgroundJob.id == job.id).first()
                if current is not None:
                    current.locked_at = None
                    current.error_code = exc.detail[:48] if isinstance(exc, HTTPException) else (
                        str(exc)[:48] if isinstance(exc, ValueError) else "qwen_processing_failed"
                    )
                    if isinstance(exc, ValueError) and str(exc) == "community_disabled":
                        current.status = "failed"
                    elif current.attempt_count < 3:
                        current.status = "queued"
                        current.available_at = datetime.now(timezone.utc) + timedelta(seconds=15 * current.attempt_count)
                    else:
                        current.status = "failed"
                    db.commit()
            except Exception:
                db.rollback()
        return True
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
