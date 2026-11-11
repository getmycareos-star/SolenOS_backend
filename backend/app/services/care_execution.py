"""Shared care-source execution coordinator.

Workflow adapters preserve their source-specific input and authorization rules.
This coordinator owns the common post-save sequence for persisted caregiver
capture: extraction from stored evidence, review-state persistence,
reconciliation, and a permission-scoped LCR read-back.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.care import CareEvent, Evidence
from app.models.capture_analysis import CaptureAnalysis
from app.services.capture_review import persist_capture_analysis
from app.services.change_intelligence_reconciliation import reconcile_person_change_intelligence
from app.services.lcr_context import build_lcr_context
from app.services.qwen_gateway import extract_review_candidates

logger = logging.getLogger(__name__)


def execute_care_source(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    source_event_id: str,
    source_evidence_id: str,
) -> dict[str, Any]:
    """Run the shared post-save lifecycle for one persisted Daily Care source.

    The source and evidence must already be committed. Model/provider failure
    never removes that source. Candidate output remains review-only and cannot
    become an LCR assertion here.
    """
    event = (
        db.query(CareEvent)
        .filter(
            CareEvent.id == source_event_id,
            CareEvent.person_id == person_id,
            CareEvent.created_by_caregiver_id == caregiver_id,
        )
        .first()
    )
    evidence = (
        db.query(Evidence)
        .filter(
            Evidence.id == source_evidence_id,
            Evidence.person_id == person_id,
            Evidence.uploaded_by_caregiver_id == caregiver_id,
        )
        .first()
    )
    if event is None or evidence is None or source_evidence_id not in (event.evidence_ids or []):
        raise ValueError("The saved source and evidence must belong to the authenticated care record.")

    # The model receives the persisted source, never the transient request body.
    source_text = evidence.source_text or ""
    candidates: dict[str, Any] | None = None
    extraction_status = "unavailable"
    try:
        candidates = extract_review_candidates(
            source_text,
            source_event_id=event.id,
            source_evidence_id=evidence.id,
        )
        extraction_status = "available"
    except Exception:
        logger.exception("Care-source extraction failed for event %s", event.id)

    observations = candidates.get("observations", []) if isinstance(candidates, dict) else []
    review_required = bool(observations)
    execution_status = {
        "source_status": "source_preserved",
        "evidence_status": "evidence_persisted",
        "extraction_status": (
            "extraction_available" if extraction_status == "available" else "extraction_failed"
        ),
        "assessment_status": (
            "assessment_pending" if extraction_status == "available" else "assessment_failed"
        ),
        "review_status": "review_required" if review_required else "review_not_required",
        "reconciliation_status": "reconciliation_pending",
        "retrieval_status": "retrieval_unavailable",
    }

    analysis = persist_capture_analysis(
        db,
        person_id=person_id,
        caregiver_id=caregiver_id,
        source_event_id=event.id,
        source_evidence_id=evidence.id,
        source_text=source_text,
        extraction_status=extraction_status,
        candidates=candidates if extraction_status == "available" else None,
        execution_status=execution_status,
        commit=False,
    )

    try:
        reconcile_person_change_intelligence(db, person_id)
        execution_status["reconciliation_status"] = "reconciliation_completed"
        try:
            projection = build_lcr_context(
                db,
                person_id,
                caregiver_id=caregiver_id,
                limit_per_source=1,
            )
            execution_status["retrieval_status"] = (
                "retrieval_available"
                if not projection.get("missing_required_sources")
                else "retrieval_unavailable"
            )
        except Exception:
            logger.exception("LCR read-back failed after capture event %s", event.id)
            execution_status["retrieval_status"] = "retrieval_unavailable"
        analysis.execution_status = dict(execution_status)
        db.commit()
        db.refresh(analysis)
    except Exception:
        # The source/evidence transaction was committed before this coordinator.
        # Roll back only derived processing, then persist a truthful failure receipt.
        db.rollback()
        execution_status["reconciliation_status"] = "reconciliation_failed"
        execution_status["retrieval_status"] = "retrieval_unavailable"
        analysis = persist_capture_analysis(
            db,
            person_id=person_id,
            caregiver_id=caregiver_id,
            source_event_id=event.id,
            source_evidence_id=evidence.id,
            source_text=source_text,
            extraction_status=extraction_status,
            candidates=candidates if extraction_status == "available" else None,
            execution_status=execution_status,
            commit=True,
        )

    return {
        "analysis_id": analysis.id,
        "extraction_status": extraction_status,
        "candidates": candidates if extraction_status == "available" else None,
        "execution_status": dict(execution_status),
    }
