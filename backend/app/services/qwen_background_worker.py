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


async def _classify_task(task_text: str, context_text: str) -> str:
    choices = ", ".join(ENABLED_WORKFLOWS)
    prompt = (
        "Classify the caregiver's request into exactly one enabled SolenOS workflow ID. "
        "Community is disabled and is not an option. Do not answer the caregiver. "
        "Return only a JSON object with one key workflow and a value copied exactly from this list: "
        + choices
    )
    raw = await asyncio.to_thread(
        qwen_chat,
        [{"role": "system", "content": prompt},
         {"role": "user", "content": json.dumps({"request": task_text[:1800], "screen_context": context_text[:3000]})}],
        options={"temperature": 0, "num_predict": 48},
    )
    try:
        value = json.loads(raw.strip()).get("workflow")
    except (json.JSONDecodeError, AttributeError):
        value = None
    if value not in ENABLED_WORKFLOWS:
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
