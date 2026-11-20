import json
import hashlib
from bisect import bisect_right
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import inspect

from app.core.database import get_db
from app.models.anonymous_session import AnonymousSession
from app.models.caregiver_access import CaregiverAccessGrant
from app.models.care_continuity import CareDeltaRun, CareStateRevision, EvidenceObservation, TRACKED_TABLES
from app.models.care import CareEvent, Evidence
from app.schemas.care_delta import CareDeltaReconcileRequest
from app.services.delta_engine import reconcile_event

router = APIRouter(prefix="/care-delta", tags=["care-delta"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _scoped_identity(request: Request, db: Session) -> tuple[str, str]:
    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if person_id and caregiver_id:
        grant = db.query(CaregiverAccessGrant.id).filter(
            CaregiverAccessGrant.person_id == str(person_id),
            CaregiverAccessGrant.caregiver_id == str(caregiver_id),
            CaregiverAccessGrant.status == "active",
            CaregiverAccessGrant.revoked_at.is_(None),
            CaregiverAccessGrant.access_scope == "full_record",
        ).first()
        if grant is None:
            raise HTTPException(status_code=403, detail="Active full care-record access is required")
        return str(person_id), str(caregiver_id)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS anonymous session is required")
    token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Anonymous session is invalid or expired")
    grant = db.query(CaregiverAccessGrant.id).filter(
        CaregiverAccessGrant.person_id == session.person_id,
        CaregiverAccessGrant.caregiver_id == session.caregiver_id,
        CaregiverAccessGrant.status == "active",
        CaregiverAccessGrant.revoked_at.is_(None),
        CaregiverAccessGrant.access_scope == "full_record",
    ).first()
    if grant is None:
        raise HTTPException(status_code=403, detail="Active full care-record access is required")
    return str(session.person_id), str(session.caregiver_id)


@router.post("/reconcile")
def reconcile_care_event(payload: CareDeltaReconcileRequest, request: Request, db: Session = Depends(get_db)):
    scoped_person_id, scoped_caregiver_id = _scoped_identity(request, db)
    if str(payload.person_id) != str(scoped_person_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    return reconcile_event(
        db, person_id=scoped_person_id, event_date=payload.event_date, source=payload.source,
        content=payload.content, source_event_id=payload.source_event_id,
        evidence_ids=payload.evidence_ids, persist=payload.persist,
        created_by_caregiver_id=scoped_caregiver_id,
    )



@router.get("/runs/{person_id}")
def list_delta_runs(person_id: str, request: Request, limit: int = 25, offset: int = 0, db: Session = Depends(get_db)):
    scoped_person_id, _ = _scoped_identity(request, db)
    if str(person_id) != str(scoped_person_id):
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    rows = db.query(CareDeltaRun).filter(
        CareDeltaRun.person_id == scoped_person_id
    ).order_by(CareDeltaRun.created_at.desc(), CareDeltaRun.id.desc()).offset(max(0, offset)).limit(max(1, min(limit, 100))).all()
    schema_inspector = inspect(db.get_bind())
    observation_history_checked = schema_inspector.has_table("evidence_observations")
    care_state_history_checked = schema_inspector.has_table("care_state_revisions")
    context_revision_times = []
    run_times = [row.created_at for row in rows if row.created_at]
    if care_state_history_checked and run_times:
        earliest_run_time = min(run_times)
        revision_rows = db.query(CareStateRevision.changed_at).filter(
            CareStateRevision.person_id == scoped_person_id,
            # Delta comparisons depend on the active change and baseline state as
            # well as the original reports. These tables are part of the same
            # revision ledger; omitting them can leave an old comparison marked
            # current after a baseline is superseded or a change is resolved.
            CareStateRevision.target_type.in_(sorted(TRACKED_TABLES)),
            CareStateRevision.changed_at > earliest_run_time,
        ).all()
        context_revision_times = sorted(_as_utc(item[0]) for item in revision_rows if item[0])

    output = []
    for row in rows:
        try:
            delta = json.loads(row.delta_json)
        except (TypeError, json.JSONDecodeError):
            delta = None
        source_ids = [str(value) for value in (row.evidence_ids or []) if value]
        changed_observation_count = 0
        changed_source_count = 0
        changed_context_count = 0
        freshness_reasons = []
        if row.created_at and care_state_history_checked:
            # A Delta run depends on more than its incoming event. Any persisted
            # care-state revision can change the comparison basis, including
            # active changes, baselines, and situation threads.
            changed_context_count = len(context_revision_times) - bisect_right(
                context_revision_times, _as_utc(row.created_at)
            )
            if changed_context_count:
                freshness_reasons.append("care_record_context_changed_after_review")
        if row.source_event_id:
            source_event = db.query(CareEvent).filter(
                CareEvent.person_id == scoped_person_id,
                CareEvent.id == row.source_event_id,
            ).first()
            if source_event is None:
                changed_source_count += 1
                freshness_reasons.append("source_event_unavailable")
            elif row.created_at:
                for source_time in (source_event.updated_at, source_event.recorded_at):
                    if source_time:
                        run_time = row.created_at
                        if source_time.tzinfo is None:
                            source_time = source_time.replace(tzinfo=timezone.utc)
                        if run_time.tzinfo is None:
                            run_time = run_time.replace(tzinfo=timezone.utc)
                        if source_time > run_time:
                            changed_source_count += 1
                            freshness_reasons.append("source_event_changed_after_review")
                            break
            if source_event:
                source_ids = list(dict.fromkeys(source_ids + [str(value) for value in (source_event.evidence_ids or []) if value]))
        if source_ids:
            evidence_rows = db.query(Evidence).filter(
                Evidence.person_id == scoped_person_id,
                Evidence.id.in_(source_ids),
            ).all()
            if len(evidence_rows) != len(set(source_ids)):
                changed_source_count += len(set(source_ids) - {str(item.id) for item in evidence_rows})
                freshness_reasons.append("linked_source_unavailable")
            if row.created_at:
                run_time = row.created_at
                if run_time.tzinfo is None:
                    run_time = run_time.replace(tzinfo=timezone.utc)
                for evidence_row in evidence_rows:
                    updated_at = getattr(evidence_row, "updated_at", None)
                    if updated_at:
                        if updated_at.tzinfo is None:
                            updated_at = updated_at.replace(tzinfo=timezone.utc)
                        if updated_at > run_time:
                            changed_source_count += 1
                            freshness_reasons.append("source_document_changed_after_review")
            if row.created_at and observation_history_checked:
                observation_rows = db.query(EvidenceObservation).filter(
                    EvidenceObservation.person_id == scoped_person_id,
                    EvidenceObservation.evidence_id.in_(source_ids),
                    EvidenceObservation.recorded_at > row.created_at,
                ).all()
                changed_observation_count = len(observation_rows)
                if changed_observation_count:
                    changed_source_count += changed_observation_count
                    freshness_reasons.append("source_observation_added_or_corrected_after_review")
        source_freshness = {
            "status": "stale" if changed_source_count or changed_context_count else "current" if (source_ids or row.source_event_id) and observation_history_checked and care_state_history_checked else "not_checked",
            "changed_source_count": changed_source_count,
            "changed_observation_count": changed_observation_count,
            "changed_context_count": changed_context_count,
            "reasons": sorted(set(freshness_reasons)),
        }
        output.append({
            "id": row.id,
            "person_id": row.person_id,
            "source": row.source,
            "event_date": row.event_date,
            "source_event_id": row.source_event_id,
            "evidence_ids": row.evidence_ids or [],
            "model_name": row.model_name,
            "created_by_caregiver_id": row.created_by_caregiver_id,
            "created_at": row.created_at,
            "source_freshness": source_freshness,
            "delta": delta,
        })
    return output
