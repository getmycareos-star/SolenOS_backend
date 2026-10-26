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
    "audit_review",
)


def _strong_workflow_hint(task_text: str, context_text: str = "") -> str | None:
    """Resolve unmistakable task language internally before asking a small model to classify it.

    This is invisible to caregivers. Qwen still generates every completed suggestion;
    the hints prevent the 0.5B classifier from confusing corrections, summaries,
    access-state questions, and other high-impact workflows.
    """
    text = f"{task_text}\\n{context_text}".casefold()
    rules = (
        ("after_appointment", r"after[- ]appointment|after the appointment|after today.s appointment|clinician notes"),
        ("two_week_review", r"last fourteen days|last 14 days|past 14 days|two[- ]week review|last two weeks"),
        ("delta_engine", r"delta engine|review candidate|candidate change|reconcile|against the prior note"),
        ("care_situation_change", r"compare these two|compare.*yesterday|what changed|situation change"),
        ("uncertainty_contradictions", r"contradict|conflict(?:ing)? notes|two notes conflict|ambiguous date"),
        ("evidence_provenance", r"provenance|source and certainty|label the source|source of this claim"),
        ("returning_caregiver", r"returning caregiver|returning to caregiving|returning after"),
        ("professional_summary", r"professional-facing summary|professional summary"),
        ("appointment", r"prepare questions for.*appointment|appointment preparation"),
        ("handoff", r"\\bhandoff\\b|hand-off"),
        ("open_loops", r"unresolved question|open loop|follow-up|need to confirm"),
        ("decision_outcome", r"decision and outcome|decision from its outcome|decision.*outcome"),
        ("reminders_notifications", r"\\breminder\\b|\\bnotification delivery\\b|reminder.*delivered"),
        ("care_coordination", r"care coordination|caregiver.*available|roles or assignments|responsibilities.*care"),
        ("companion", r"feel overwhelmed|respond supportively|need emotional support|talk to someone"),
        ("caregiver_feedback", r"wrong date|earlier entry|correct an existing care entry|caregiver feedback"),
        ("sharing_export", r"shareable summary|\\bexport\\b|do not say it has been sent"),
        ("roles_access", r"professional access|role or permission|who can access|access permissions"),
        ("account_recovery", r"cannot sign in|can.t sign in|account recovery|recover my account|reset my password"),
        ("settings_security", r"multi-factor|two-factor|\\bmfa\\b|security settings|privacy settings"),
        ("record_lifecycle", r"\\barchiv(?:e|ed|ing)\\b|restore the record|delete the record|pin the record"),
        ("offline_sync", r"\\bsync(?:ed|hronized| status)?\\b|offline changes"),
        ("ai_reliability", r"assistant request returned an error|inference failed|qwen availability|request failed"),
        ("audit_review", r"audit trail|audit records|review candidate status|audit review"),
        ("medication_day", r"medication was taken|medicine was taken|medication-day|organize this medication note"),
        ("care_history_search", r"search the available care history|search.*history|find.*in.*history|history search"),
        ("living_care_record", r"summarize.*care record|living care record summary|available care record"),
        ("document", r"document excerpt|exact sentence from a document|\\bdocument\\b"),
        ("add_record", r"add a record|add record|proposed record entry|draft the correction"),
        ("daily_capture", r"today.s caregiver observation|daily capture|organize today.s observation|caregiver note"),
    )
    for workflow, pattern in rules:
        if re.search(pattern, text):
            return workflow
    return None


async def _classify_task(task_text: str, context_text: str) -> str:
    hinted = _strong_workflow_hint(task_text, context_text)
    if hinted:
        return hinted
    choices = ", ".join(ENABLED_WORKFLOWS)
    descriptions = {
        "daily_capture": "structure today's newly reported observation",
        "add_record": "draft a new record entry or correction, without saving it",
        "document": "explain or extract text from an uploaded document",
        "living_care_record": "summarize the current longitudinal care record",
        "care_history_search": "search existing recorded history for an event",
        "care_situation_change": "compare current and prior observations",
        "delta_engine": "reconcile candidate additions or changes against prior evidence",
        "evidence_provenance": "label source and certainty of claims",
        "uncertainty_contradictions": "surface conflicting or ambiguous statements",
        "returning_caregiver": "orient a returning caregiver from existing records",
        "appointment": "prepare for an upcoming appointment",
        "after_appointment": "organize notes after an appointment",
        "handoff": "draft a care handoff",
        "professional_summary": "prepare a professional-facing summary",
        "open_loops": "identify explicitly unresolved questions or follow-ups",
        "two_week_review": "review recorded events from the last fourteen days",
        "decision_outcome": "separate a reported decision from its reported outcome",
        "medication_day": "organize a caregiver-reported medication observation",
        "reminders_notifications": "check or interpret reminder and notification records",
        "care_coordination": "organize caregiver availability and responsibilities",
        "companion": "respond to an emotional-support or everyday conversational request",
        "caregiver_feedback": "capture feedback that an existing entry is wrong",
        "sharing_export": "prepare a summary for caregiver-reviewed sharing or export",
        "roles_access": "explain roles or access based on authoritative records",
        "account_recovery": "help with sign-in or account recovery",
        "settings_security": "interpret security and privacy settings",
        "record_lifecycle": "explain pinning, archiving, restoring or deleting records",
        "offline_sync": "interpret offline changes and confirmed synchronization",
        "ai_reliability": "explain an assistant availability or request failure",
        "audit_review": "summarize audit entries and review-candidate status",
    }
    prompt = (
        "Classify the request into exactly one enabled SolenOS workflow. Community is disabled. "
        "Do not answer the caregiver. Select the best match using these meanings: "
        + json.dumps(descriptions, ensure_ascii=False)
        + ". Return JSON with exactly one key, workflow, whose value is one of: "
        + choices
    )
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
        value = json.loads(raw.strip()).get("workflow")
    except (json.JSONDecodeError, AttributeError):
        value = None
    if value not in ENABLED_WORKFLOWS:
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
                    if current.attempt_count < 3:
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
