from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, date, timezone
import json
import logging
import hashlib
from app.core.database import get_db
from app.api.registered_actions import router as registered_actions_router
from app.services.qwen_gateway import extract_review_candidates
from app.services.capture_review import persist_capture_analysis, persist_source_grounded_daily_observations
from app.services.uncertainty import persist_source_clarification_questions
from app.services.change_intelligence_reconciliation import reconcile_person_change_intelligence
from app.services.care_continuity_execution import execute_care_continuity_reconciliation
from app.services.lcr_context import build_lcr_context
from app.models.anonymous_session import AnonymousSession
from app.models.capture_analysis import CaptureAnalysis as CaptureAnalysisModel
from app.models.qwen_background_job import QwenBackgroundJob, QwenJobEvent
from app.models.collaboration import ConsentRecord, EvidenceUpload
from app.models.care_continuity import (
    EvidenceFreshnessAssessment,
    EvidenceCompletenessAssessment,
    CareAssertion,
)
from app.models.care import (
    Person as PersonModel,
    Caregiver as CaregiverModel,
    Evidence as EvidenceModel,
    CareEvent as CareEventModel,
    Insight as InsightModel,
    Correction as CorrectionModel,
)
from app.models.memory import (
    ReasoningMemory as ReasoningMemoryModel,
    Observation as ObservationModel,
    LearningEvent as LearningEventModel,
)
from app.models.time_location import (
    CareWindow as CareWindowModel,
    Appointment as AppointmentModel,
    CareTransition as CareTransitionModel,
    Location as LocationModel,
    DailyIntelligence as DailyIntelligenceModel,
    TimezoneContext as TimezoneContextModel,
    TemporalRelationship as TemporalRelationshipModel,
)
from app.models.temporal import (
    TemporalFact as TemporalFactModel,
    TemporalRelation as TemporalRelationModel,
    TemporalReferenceFrame as TemporalReferenceFrameModel,
)
from app.schemas.care import (
    PersonCreate,
    Person,
    CaregiverCreate,
    Caregiver as CaregiverSchema,
    EvidenceCreate,
    Evidence as EvidenceSchema,
    CareEventCreate,
    CareEvent as CareEventSchema,
    InsightCreate,
    Insight as InsightSchema,
    CorrectionCreate,
    Correction as CorrectionSchema,
)
from app.schemas.memory import (
    ReasoningMemoryCreate,
    ReasoningMemory as ReasoningMemorySchema,
    ObservationCreate,
    Observation as ObservationSchema,
    LearningEvent as LearningEventSchema,
    ReasoningSummary,
    ObservationTrend,
)
from app.schemas.time_location import (
    CareWindow as CareWindowSchema,
    AppointmentCreate,
    Appointment as AppointmentSchema,
    CareTransitionCreate,
    CareTransition as CareTransitionSchema,
    LocationCreate,
    Location as LocationSchema,
    DailyIntelligence as DailyIntelligenceSchema,
    TimezoneContext as TimezoneContextSchema,
    TemporalRelationship as TemporalRelationshipSchema,
    NaturalLanguageTimeParse,
)
from app.schemas.temporal import (
    TemporalFactCreate,
    TemporalFactUpdate,
    TemporalFact as TemporalFactSchema,
    TemporalRelationCreate,
    TemporalRelation as TemporalRelationSchema,
    TemporalReferenceFrameCreate,
    TemporalReferenceFrame as TemporalReferenceFrameSchema,
    TemporalContradictionCreate,
    TemporalContradictionUpdate,
    TemporalContradiction as TemporalContradictionSchema,
    RelativeTimeResolution,
    TemporalViewAtPoint,
    SubjectTemporalView,
)
from app.services.learning import LearningEngine, SavePipelineEvent
from app.services.improvement_learning import stage_consented_feedback
from app.services.time_intelligence import (
    parse_natural_language_time,
    calculate_care_windows,
    generate_daily_intelligence,
    update_appointment_lifecycle,
    infer_temporal_relationship,
    detect_time_patterns,
    natural_language_time_ago,
    natural_language_time_until,
    format_local,
    format_date_local,
    format_time_local,
    utc_now,
)
from app.services.location_intelligence import (
    validate_timezone,
    infer_transition_type,
    enrich_location_with_geographic_intelligence,
    format_location_for_display,
)
from app.services.temporal_intelligence import (
    create_temporal_fact,
    update_temporal_fact,
    supersede_temporal_fact,
    create_temporal_relation,
    create_temporal_reference_frame,
    resolve_relative_time,
    infer_temporal_relation,
    detect_temporal_conflicts,
    create_temporal_contradiction,
    update_temporal_contradiction,
    get_temporal_view_at_point,
    get_subject_temporal_view,
    calculate_duration,
    classify_temporal_status,
    check_temporal_conflict,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_session_scope(request: Request, db: Session, person_id: str, caregiver_id: Optional[str] = None) -> AnonymousSession:
    authorization = request.headers.get("authorization", "")
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not raw_token.strip():
        raise HTTPException(status_code=401, detail="A valid SolenOS anonymous session is required")
    token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
    session = db.query(AnonymousSession).filter(
        AnonymousSession.token_hash == token_hash,
        AnonymousSession.revoked_at.is_(None),
        AnonymousSession.expires_at > datetime.now(timezone.utc),
    ).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Anonymous session is invalid or expired")
    if str(session.person_id) != str(person_id):
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    if caregiver_id and str(session.caregiver_id) != str(caregiver_id):
        raise HTTPException(status_code=403, detail="Caregiver attribution does not match this session")
    return session


def _require_person_and_linked_caregiver(
    db: Session, person_id: str, caregiver_id: Optional[str], *, caregiver_required: bool = True
) -> None:
    """Validate record references before persisting caregiver-attributed data.

    This is an integrity check, not authentication: API session/auth enforcement
    remains a separate production requirement.
    """
    if not person_id:
        raise HTTPException(status_code=422, detail="person_id is required")
    person = db.query(PersonModel).filter(PersonModel.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if not caregiver_id:
        if caregiver_required:
            raise HTTPException(status_code=422, detail="caregiver_id is required")
        return
    caregiver = (
        db.query(CaregiverModel)
        .filter(CaregiverModel.id == caregiver_id, CaregiverModel.person_id == person_id)
        .first()
    )
    if not caregiver:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care record")


def _log_and_learn(
    db: Session,
    person_id: str,
    event_type: str,
    detail: str,
    source_type: str,
    source_id: str = None,
    caregiver_id: str = None,
):
    engine = LearningEngine(db)
    engine.log_pipeline_event(
        SavePipelineEvent(step=f"{event_type}_saved", person_id=person_id, detail=detail)
    )
    engine.record_learning_event(
        person_id, event_type, detail, source_type, source_id, caregiver_id
    )
    engine.after_save(person_id)


@router.post("/persons", response_model=Person)
def create_person(person: PersonCreate, db: Session = Depends(get_db)):
    dob = person.date_of_birth
    if isinstance(dob, str) and dob:
        try:
            dob = date.fromisoformat(dob)
        except ValueError:
            try:
                dob = datetime.strptime(dob, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="date_of_birth must be an ISO date string (YYYY-MM-DD)",
                )
    elif dob is None:
        dob = None
    db_person = PersonModel(name=person.name, date_of_birth=dob)
    db.add(db_person)
    db.commit()
    db.refresh(db_person)
    _log_and_learn(
        db,
        db_person.id,
        "person_created",
        f"Created person {db_person.name}",
        "api",
        caregiver_id=None,
    )
    return db_person


@router.get("/persons/{person_id}", response_model=Person)
def get_person(person_id: str, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, person_id)
    person = db.query(PersonModel).filter(PersonModel.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


@router.post("/caregivers", response_model=CaregiverSchema)
def create_caregiver(caregiver: CaregiverCreate, db: Session = Depends(get_db)):
    person = db.query(PersonModel).filter(PersonModel.id == caregiver.person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    existing = (db.query(CaregiverModel).filter(CaregiverModel.email == caregiver.email).first() if caregiver.email else None)
    if existing:
        raise HTTPException(status_code=409, detail="A caregiver with this email already exists")
    db_caregiver = CaregiverModel(**caregiver.model_dump())
    db.add(db_caregiver)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Caregiver email conflicts with an existing record") from exc
    db.refresh(db_caregiver)
    _log_and_learn(
        db,
        db_caregiver.person_id,
        "caregiver_added",
        f"Added caregiver {db_caregiver.name}",
        "api",
        caregiver_id=db_caregiver.id,
    )
    return db_caregiver


@router.get("/caregivers/{person_id}", response_model=List[CaregiverSchema])
def list_caregivers(person_id: str, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, person_id)
    person = db.query(PersonModel).filter(PersonModel.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return (
        db.query(CaregiverModel)
        .filter(CaregiverModel.person_id == person_id)
        .order_by(CaregiverModel.created_at.asc())
        .all()
    )


@router.post("/evidence", response_model=EvidenceSchema)
def create_evidence(evidence: EvidenceCreate, request: Request, db: Session = Depends(get_db)):
    if (
        getattr(request.state, "caregiver_access_scope", "full_record") == "documents_only"
        and str(evidence.type).lower() != "document"
    ):
        raise HTTPException(status_code=403, detail="This caregiver can add documents, not other care updates.")
    # Evidence confidence must not be supplied by a client until a task-specific
    # calibration method has validation evidence. Preserve the field for wire
    # compatibility, but reject numeric values rather than silently trusting them.
    if evidence.confidence is not None:
        raise HTTPException(
            status_code=422,
            detail="Numeric evidence confidence is unavailable until a calibrated assessment method is configured.",
        )
    # Reference validation is not authentication: bind both client-supplied IDs
    # to the server-issued bearer session before writing any evidence.
    _require_session_scope(
        request, db, evidence.person_id, evidence.uploaded_by_caregiver_id
    )
    _require_person_and_linked_caregiver(
        db, evidence.person_id, evidence.uploaded_by_caregiver_id
    )
    for field_name, caregiver_id in (
        ("reported_by_caregiver_id", evidence.reported_by_caregiver_id),
        ("collected_by_caregiver_id", evidence.collected_by_caregiver_id),
    ):
        if caregiver_id and not db.query(CaregiverModel.id).filter(
            CaregiverModel.id == caregiver_id,
            CaregiverModel.person_id == evidence.person_id,
        ).first():
            raise HTTPException(
                status_code=422,
                detail=f"{field_name} must identify a caregiver linked to this care record",
            )
    if evidence.parent_evidence_id:
        if evidence.parent_evidence_id == getattr(evidence, "id", None):
            raise HTTPException(status_code=422, detail="Evidence cannot be its own parent")
        parent = db.query(EvidenceModel.id).filter(
            EvidenceModel.id == evidence.parent_evidence_id,
            EvidenceModel.person_id == evidence.person_id,
        ).first()
        if parent is None:
            raise HTTPException(status_code=422, detail="Parent evidence must exist in the same care record")
    evidence_payload = evidence.model_dump()
    ingestion_key = evidence_payload.get("ingestion_key")
    request_fingerprint = None
    if ingestion_key:
        canonical_request = evidence.model_dump(mode="json", exclude={"ingestion_key"})
        request_fingerprint = hashlib.sha256(
            json.dumps(canonical_request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        existing = db.query(EvidenceModel).filter(
            EvidenceModel.person_id == evidence.person_id,
            EvidenceModel.ingestion_key == ingestion_key,
        ).first()
        if existing is not None:
            if existing.ingestion_request_sha256 == request_fingerprint:
                return existing
            raise HTTPException(
                status_code=409,
                detail="This ingestion key was already used for a different evidence payload",
            )
    evidence_payload["ingestion_request_sha256"] = request_fingerprint
    db_evidence = EvidenceModel(**evidence_payload)
    db.add(db_evidence)
    try:
        db.commit()
    except IntegrityError as exc:
        # A concurrent request may have won the unique-key race. Resolve it as
        # a replay only when the persisted canonical payload fingerprint matches.
        db.rollback()
        if ingestion_key:
            existing = db.query(EvidenceModel).filter(
                EvidenceModel.person_id == evidence.person_id,
                EvidenceModel.ingestion_key == ingestion_key,
            ).first()
            if existing is not None and existing.ingestion_request_sha256 == request_fingerprint:
                return existing
            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail="This ingestion key was already used for a different evidence payload",
                ) from exc
        raise
    db.refresh(db_evidence)
    _log_and_learn(
        db,
        db_evidence.person_id,
        "evidence_added",
        f"Added {evidence.type} evidence",
        "evidence",
        source_id=db_evidence.id,
        caregiver_id=evidence.uploaded_by_caregiver_id,
    )
    execution = None
    try:
        execution = execute_care_continuity_reconciliation(
            db,
            db_evidence.person_id,
            workflow="add_record_evidence",
            source_caregiver_id=evidence.uploaded_by_caregiver_id,
            source_evidence_id=db_evidence.id,
        )
        db.commit()
        execution["stages"]["reconciliation"] = "committed"
    except Exception:
        db.rollback()
        db.refresh(db_evidence)
        execution = {
            "contract_version": "1.0",
            "workflow": "add_record_evidence",
            "person_id": db_evidence.person_id,
            "source_evidence_id": db_evidence.id,
            "stages": {
                "source_preservation": "verified",
                "evidence": "verified",
                "reconciliation": "unavailable",
            },
            "transaction_owner": "caller",
        }
        logger.exception(
            "Shared care-continuity execution failed after durable Add Record evidence %s",
            db_evidence.id,
        )
    db_evidence.care_continuity_execution = execution
    return db_evidence


@router.get("/evidence/{person_id}", response_model=List[EvidenceSchema])
def list_evidence(
    person_id: str,
    request: Request,
    ids: Optional[List[str]] = Query(default=None),
    db: Session = Depends(get_db),
):
    _require_session_scope(request, db, person_id)

    query = db.query(EvidenceModel).filter(
        EvidenceModel.person_id == person_id,
        (EvidenceModel.evidence_status.is_(None)) | (EvidenceModel.evidence_status != "deleted_by_caregiver"),
    )
    if getattr(request.state, "caregiver_access_scope", "full_record") == "documents_only":
        query = query.filter(EvidenceModel.type == "document")
    if ids is not None:
        # An explicit filter is always bounded and never falls back to the
        # full evidence collection. Empty/blank filters mean no rows.
        requested_ids = list(dict.fromkeys(value.strip() for value in ids if value and value.strip()))
        if len(requested_ids) > 100:
            raise HTTPException(status_code=422, detail="At most 100 evidence IDs may be requested at once")
        if not requested_ids:
            return []
        query = query.filter(EvidenceModel.id.in_(requested_ids))

    rows = query.order_by(EvidenceModel.uploaded_at.asc(), EvidenceModel.id.asc()).all()
    if ids is not None:
        returned_ids = {row.id for row in rows}
        if returned_ids != set(requested_ids):
            # Do not reveal whether an unknown ID exists under another person.
            raise HTTPException(status_code=404, detail="One or more evidence items were not found in this care record")
    # Evidence.extra_metadata is stored as TEXT for migration compatibility,
    # while the public schema exposes a structured object. Decode it at the
    # boundary instead of leaking a JSON string that fails response validation.
    evidence_ids = [row.id for row in rows]
    freshness_by_evidence = {}
    if evidence_ids:
        freshness_rows = (
            db.query(EvidenceFreshnessAssessment)
            .filter(
                EvidenceFreshnessAssessment.person_id == person_id,
                EvidenceFreshnessAssessment.evidence_id.in_(evidence_ids),
            )
            .order_by(EvidenceFreshnessAssessment.assessed_at.asc(), EvidenceFreshnessAssessment.id.asc())
            .all()
        )
        for assessment in freshness_rows:
            freshness_by_evidence[assessment.evidence_id] = assessment

    completeness_by_evidence = {}
    if evidence_ids:
        completeness_rows = (
            db.query(EvidenceCompletenessAssessment)
            .filter(
                EvidenceCompletenessAssessment.person_id == person_id,
                EvidenceCompletenessAssessment.subject_type == "evidence",
                EvidenceCompletenessAssessment.subject_id.in_(evidence_ids),
            )
            .order_by(EvidenceCompletenessAssessment.assessed_at.asc(), EvidenceCompletenessAssessment.id.asc())
            .all()
        )
        for assessment in completeness_rows:
            completeness_by_evidence[assessment.subject_id] = assessment

    response = []
    for row in rows:
        item = {
            column.name: getattr(row, column.name)
            for column in EvidenceModel.__table__.columns
        }
        metadata = item.get("extra_metadata")
        if isinstance(metadata, str):
            try:
                parsed = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                parsed = {"metadata_integrity": "invalid_json"}
            item["extra_metadata"] = parsed if isinstance(parsed, dict) else {
                "metadata_integrity": "unexpected_json_type"
            }
        elif not isinstance(metadata, dict):
            item["extra_metadata"] = {}

        # A document/photo row without its original binary is an inaccessible
        # source, not a complete document record. Report the state explicitly;
        # byte-level integrity is checked by the download endpoint.
        integrity = dict(item["extra_metadata"].get("source_integrity") or {})
        if item.get("type") in {"document", "medication_photo", "care_photo"}:
            blob_exists = (
                db.query(EvidenceUpload.evidence_id)
                .filter(EvidenceUpload.evidence_id == row.id)
                .first()
                is not None
            )
            integrity["original_binary_available"] = blob_exists
            integrity["original_binary_status"] = (
                "available_unverified_until_download" if blob_exists else "missing"
            )

        freshness = freshness_by_evidence.get(row.id)
        integrity["freshness_status"] = freshness.freshness_status if freshness else "not_assessed"
        if freshness:
            integrity["freshness_assessment"] = {
                "assessment_id": freshness.id,
                "policy_id": freshness.policy_id,
                "policy_version": freshness.policy_version,
                "assessed_at": freshness.assessed_at.isoformat(),
                "source_time": freshness.source_time.isoformat() if freshness.source_time else None,
                "source_time_kind": freshness.source_time_kind,
                "age_days": freshness.age_days,
                "rationale": freshness.rationale,
            }

        completeness = completeness_by_evidence.get(row.id)
        integrity["completeness_status"] = completeness.assessment_status if completeness else "not_assessed"
        if completeness:
            integrity["completeness_assessment"] = {
                "assessment_id": completeness.id,
                "policy_id": completeness.policy_id,
                "policy_version": completeness.policy_version,
                "assessed_at": completeness.assessed_at.isoformat(),
                "required_fields": list(completeness.required_fields or []),
                "satisfied_fields": list(completeness.satisfied_fields or []),
                "missing_fields": list(completeness.missing_fields or []),
                "assessment_basis": completeness.assessment_basis,
                "semantic_sufficiency": completeness.semantic_sufficiency,
                "rationale": completeness.rationale,
            }
        item["extra_metadata"]["source_integrity"] = integrity
        response.append(item)
    return response


def _save_daily_capture_event(payload: dict, event_type: str, title: str, request: Request, db: Session):
    person_id = payload.get("person_id")
    caregiver_id = payload.get("caregiver_id") or payload.get("created_by_caregiver_id")
    if not isinstance(person_id, str) or not person_id.strip():
        raise HTTPException(status_code=422, detail="person_id is required")
    if not isinstance(caregiver_id, str) or not caregiver_id.strip():
        raise HTTPException(status_code=422, detail="caregiver_id is required")

    _require_session_scope(request, db, person_id, caregiver_id)
    capture_idempotency_key = payload.get("capture_idempotency_key")
    if capture_idempotency_key is not None and (
        not isinstance(capture_idempotency_key, str)
        or not 8 <= len(capture_idempotency_key) <= 128
    ):
        raise HTTPException(status_code=422, detail="capture_idempotency_key must contain 8 to 128 characters")
    person_query = db.query(PersonModel).filter(PersonModel.id == person_id)
    # Serialize all idempotent Daily Care writes per care recipient so
    # concurrent retries cannot both pass the lookup on PostgreSQL.
    if event_type == "quick_daily_checkin" or capture_idempotency_key:
        person_query = person_query.with_for_update()
    person = person_query.first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    caregiver = (
        db.query(CaregiverModel)
        .filter(CaregiverModel.id == caregiver_id, CaregiverModel.person_id == person_id)
        .first()
    )
    if not caregiver:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care record")

    occurred_at = datetime.now(timezone.utc)
    event_time_provenance = "server_recorded_at"
    supplied_event_time = payload.get("occurred_at")
    if supplied_event_time is not None:
        if not isinstance(supplied_event_time, str) or not supplied_event_time.strip():
            raise HTTPException(status_code=422, detail="occurred_at must be an ISO datetime with a timezone offset")
        try:
            parsed_event_time = datetime.fromisoformat(supplied_event_time.replace("Z", "+00:00"))
            if parsed_event_time.tzinfo is None or parsed_event_time.utcoffset() is None:
                raise ValueError("Timezone offset is required")
            occurred_at = parsed_event_time.astimezone(timezone.utc)
            event_time_provenance = "caregiver_reported_at"
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="occurred_at must be an ISO datetime with a timezone offset")

    # Keep the submitted structured fields intact in the event record. The
    # existing schema has no separate medication/vitals/scales tables yet.
    detail = {key: value for key, value in payload.items() if key not in {"person_id", "caregiver_id", "created_by_caregiver_id"}}
    quick_check_in = detail.get("quick_check_in")
    capture_request_fingerprint = None
    if event_type in {"structured_daily_capture", "daily_care_entry"} and capture_idempotency_key:
        fingerprint_payload = {
            key: value for key, value in detail.items()
            if key not in {"capture_idempotency_key", "capture_request_fingerprint"}
        }
        capture_request_fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()
        # Use the indexed immutable evidence-ingestion key to reject changed
        # content before scanning event JSON or touching the extraction worker.
        # A reused key can never silently create a second care event.
        capture_ingestion_key = f"daily-capture:{caregiver_id}:{capture_idempotency_key}"[:128]
        existing_capture_evidence = db.query(EvidenceModel).filter(
            EvidenceModel.person_id == person_id,
            EvidenceModel.ingestion_key == capture_ingestion_key,
        ).first()
        if (
            existing_capture_evidence is not None
            and existing_capture_evidence.ingestion_request_sha256
            and existing_capture_evidence.ingestion_request_sha256 != capture_request_fingerprint
        ):
            raise HTTPException(status_code=409, detail="This Daily Care request key was already used for different content")
        existing_events = db.query(CareEventModel).filter(
            CareEventModel.person_id == person_id,
            CareEventModel.created_by_caregiver_id == caregiver_id,
            CareEventModel.event_type == event_type,
            CareEventModel.description.like(f'%"{capture_idempotency_key}"%'),
        ).all()
        for existing_event in existing_events:
            try:
                existing_detail = json.loads(existing_event.description or "{}")
            except (TypeError, ValueError):
                continue
            if existing_detail.get("capture_idempotency_key") != capture_idempotency_key:
                continue
            if existing_detail.get("capture_request_fingerprint") != capture_request_fingerprint:
                raise HTTPException(status_code=409, detail="This Daily Care request key was already used for different content")
            linked_evidence_ids = list(existing_event.evidence_ids or [])
            if not linked_evidence_ids:
                ingestion_key = f"daily-capture:{caregiver_id}:{capture_idempotency_key}"[:128]
                evidence = db.query(EvidenceModel).filter(
                    EvidenceModel.person_id == person_id,
                    EvidenceModel.ingestion_key == ingestion_key,
                ).first()
                if evidence is None:
                    source_payload = {
                        "original_capture": {
                            key: value for key, value in existing_detail.items()
                            if key not in {"capture_idempotency_key", "capture_request_fingerprint"}
                        }
                    }
                    evidence = EvidenceModel(
                        person_id=person_id,
                        type="note",
                        evidence_kind="caregiver_report",
                        epistemic_status="caregiver_reported",
                        source_text=json.dumps(source_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str),
                        uploaded_by_caregiver_id=caregiver_id,
                        reported_by_caregiver_id=caregiver_id,
                        collected_by_caregiver_id=caregiver_id,
                        reported_by_label="Caregiver",
                        source_type="caregiver_report",
                        origin_channel=event_type,
                        collection_method="caregiver_form",
                        time_provenance="server_recorded_at",
                        occurred_at=existing_event.occurred_at,
                        ingestion_key=ingestion_key,
                        ingestion_request_sha256=capture_request_fingerprint,
                    )
                    db.add(evidence)
                    db.flush()
                linked_evidence_ids = [evidence.id]
                existing_event.evidence_ids = linked_evidence_ids
                db.commit()
                db.refresh(existing_event)
            existing_analysis = (
                db.query(CaptureAnalysisModel)
                .filter(
                    CaptureAnalysisModel.person_id == person_id,
                    CaptureAnalysisModel.caregiver_id == caregiver_id,
                    CaptureAnalysisModel.source_event_id == existing_event.id,
                )
                .order_by(CaptureAnalysisModel.created_at.desc())
                .first()
            )
            existing_job = db.query(QwenBackgroundJob).filter(
                QwenBackgroundJob.person_id == person_id,
                QwenBackgroundJob.caregiver_id == caregiver_id,
                QwenBackgroundJob.workflow == "daily_capture_extraction",
                QwenBackgroundJob.source_event_ids_json == json.dumps([existing_event.id]),
            ).order_by(QwenBackgroundJob.created_at.desc()).first()
            existing_observations = (
                existing_analysis.candidates.get("observations", [])
                if existing_analysis and isinstance(existing_analysis.candidates, dict)
                else []
            )
            reviewable_candidates = [
                item for item in existing_observations
                if isinstance(item, dict)
                and item.get("review_status", "pending_review") in {
                    "pending_review", "corrected_pending_approval", "corrected_by_caregiver",
                }
            ]
            if (
                existing_analysis
                and existing_analysis.extraction_status == "available"
                and isinstance(existing_analysis.candidates, dict)
                and detail.get("capture_kind") != "natural_language_update"
            ):
                try:
                    persist_source_clarification_questions(
                        db,
                        person_id=person_id,
                        caregiver_id=caregiver_id,
                        source_kind="daily_capture_extraction",
                        source_record_type="care_event",
                        source_record_id=existing_event.id,
                        evidence_ids=linked_evidence_ids,
                        questions=existing_analysis.candidates.get("open_questions") or [],
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("Could not backfill saved Daily Care clarification questions for event %s", existing_event.id)
            return {
                "success": True,
                "id": existing_event.id,
                "event_type": existing_event.event_type,
                "recorded_at": existing_event.recorded_at,
                "evidence_ids": linked_evidence_ids,
                "idempotent_replay": True,
                "qwen_extraction_status": existing_analysis.extraction_status if existing_analysis else (
                    "queued" if existing_job and existing_job.status in {"queued", "processing"}
                    else "available" if existing_job and existing_job.status == "completed"
                    else "failed" if existing_job and existing_job.status == "failed"
                    else "unavailable"
                ),
                "qwen_candidates": existing_analysis.candidates if existing_analysis else None,
                "qwen_analysis_id": existing_analysis.id if existing_analysis else None,
                "qwen_job_id": existing_job.id if existing_job else None,
                "qwen_processing_status": existing_job.status if existing_job else "unavailable",
                "qwen_review_required": bool(
                    existing_analysis
                    and existing_analysis.extraction_status == "available"
                    and reviewable_candidates
                ),
                "execution_status": (
                    existing_analysis.execution_status
                    if existing_analysis and existing_analysis.execution_status
                    else {
                        "source_preservation": "verified",
                        "evidence": "verified" if linked_evidence_ids else "not_applicable",
                        "extraction": (
                            "available" if existing_analysis and existing_analysis.extraction_status == "available"
                            else "failed"
                        ),
                        "assessment": "pending",
                        "review": "required" if reviewable_candidates else "not_required",
                        "reconciliation": "pending",
                        "longitudinal_retrieval": "not_verified",
                    }
                ),
                "qwen_written_to_lcr": False,
            }
        detail["capture_request_fingerprint"] = capture_request_fingerprint
    if event_type == "quick_daily_checkin" and isinstance(quick_check_in, dict):
        idempotency_key = quick_check_in["idempotency_key"]
        fingerprint_payload = {
            "kind": quick_check_in.get("kind"),
            "selected_areas": quick_check_in.get("selected_areas"),
            "note": quick_check_in.get("note"),
            "notes": payload.get("notes"),
        }
        request_fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        existing_events = db.query(CareEventModel).filter(
            CareEventModel.person_id == person_id,
            CareEventModel.created_by_caregiver_id == caregiver_id,
            CareEventModel.event_type == "quick_daily_checkin",
            CareEventModel.description.like(f'%"{idempotency_key}"%'),
        ).all()
        for existing_event in existing_events:
            try:
                existing_detail = json.loads(existing_event.description or "{}")
                existing_quick = existing_detail.get("quick_check_in", {})
            except (TypeError, ValueError):
                continue
            if existing_quick.get("idempotency_key") == idempotency_key:
                if existing_quick.get("request_fingerprint") != request_fingerprint:
                    raise HTTPException(status_code=409, detail="This check-in key was already used for different content")
                linked_evidence_ids = list(existing_event.evidence_ids or [])
                if not linked_evidence_ids:
                    ingestion_key = f"daily-checkin:{caregiver_id}:{idempotency_key}"[:128]
                    evidence = db.query(EvidenceModel).filter(
                        EvidenceModel.person_id == person_id,
                        EvidenceModel.ingestion_key == ingestion_key,
                    ).first()
                    if evidence is None:
                        source_payload = {
                            "original_notes": existing_detail.get("notes") or "",
                            "quick_check_in": {
                                key: value for key, value in existing_quick.items()
                                if key != "request_fingerprint"
                            },
                        }
                        evidence = EvidenceModel(
                            person_id=person_id,
                            type="note",
                            evidence_kind="caregiver_report",
                            epistemic_status="caregiver_reported",
                            source_text=json.dumps(source_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                            uploaded_by_caregiver_id=caregiver_id,
                            reported_by_caregiver_id=caregiver_id,
                            collected_by_caregiver_id=caregiver_id,
                            reported_by_label="Caregiver",
                            source_type="caregiver_report",
                            origin_channel="daily_care_quick_checkin",
                            collection_method="caregiver_check_in",
                            time_provenance="caregiver_reported_at",
                            reported_at=datetime.fromisoformat(existing_quick["caregiver_reported_at"].replace("Z", "+00:00")),
                            occurred_at=existing_event.occurred_at,
                            ingestion_key=ingestion_key,
                        )
                        db.add(evidence)
                        db.flush()
                    linked_evidence_ids = [evidence.id]
                    existing_event.evidence_ids = linked_evidence_ids
                    db.commit()
                    db.refresh(existing_event)
                return {
                    "success": True,
                    "id": existing_event.id,
                    "event_type": existing_event.event_type,
                    "recorded_at": existing_event.recorded_at,
                    "evidence_ids": linked_evidence_ids,
                    "idempotent_replay": True,
                }
        quick_check_in["request_fingerprint"] = request_fingerprint
    evidence = None
    if event_type == "quick_daily_checkin" and isinstance(quick_check_in, dict):
        # Keep the original caregiver report as append-only source evidence.
        # This records provenance; it does not verify the reported care state.
        source_payload = {
            "original_notes": payload.get("notes") or "",
            "quick_check_in": {
                key: value for key, value in quick_check_in.items()
                if key != "request_fingerprint"
            },
        }
        evidence = EvidenceModel(
            person_id=person_id,
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text=json.dumps(source_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            uploaded_by_caregiver_id=caregiver_id,
            reported_by_caregiver_id=caregiver_id,
            collected_by_caregiver_id=caregiver_id,
            reported_by_label="Caregiver",
            source_type="caregiver_report",
            origin_channel="daily_care_quick_checkin",
            collection_method="caregiver_check_in",
            time_provenance="caregiver_reported_at",
            reported_at=datetime.fromisoformat(quick_check_in["caregiver_reported_at"].replace("Z", "+00:00")),
            occurred_at=occurred_at,
            ingestion_key=f"daily-checkin:{caregiver_id}:{quick_check_in['idempotency_key']}"[:128],
        )
        db.add(evidence)
        db.flush()
    elif event_type in {"structured_daily_capture", "daily_care_entry"}:
        # Ordinary Daily Care captures need the same durable source lineage as
        # quick check-ins. Preserve the submitted fields verbatim as caregiver
        # report evidence; this does not verify the reported care state.
        evidence = EvidenceModel(
            person_id=person_id,
            type="note",
            evidence_kind="caregiver_report",
            epistemic_status="caregiver_reported",
            source_text=json.dumps(
                {"original_capture": detail},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
            uploaded_by_caregiver_id=caregiver_id,
            reported_by_caregiver_id=caregiver_id,
            collected_by_caregiver_id=caregiver_id,
            reported_by_label="Caregiver",
            source_type="caregiver_report",
            origin_channel=event_type,
            collection_method="caregiver_form",
            time_provenance=event_time_provenance,
            occurred_at=occurred_at,
            ingestion_key=(
                f"daily-capture:{caregiver_id}:{capture_idempotency_key}"[:128]
                if capture_idempotency_key else None
            ),
            ingestion_request_sha256=capture_request_fingerprint,
        )
        db.add(evidence)
        db.flush()
    event = CareEventModel(
        person_id=person_id,
        event_type=event_type,
        status="recorded",
        occurred_at=occurred_at,
        title=title,
        description=json.dumps(detail, ensure_ascii=False, default=str),
        evidence_ids=[evidence.id] if evidence is not None else [],
        created_by_caregiver_id=caregiver_id,
        time_provenance=event_time_provenance,
        tags=["daily_capture"],
    )
    db.add(event)
    db.flush()
    if evidence is not None:
        event.evidence_ids = [evidence.id]
    db.commit()
    db.refresh(event)
    # The event is the primary user-requested write. Learning telemetry is
    # secondary and must not make a committed capture look unsaved, which could
    # prompt a caregiver to retry and create duplicate entries.
    learning_pipeline_status = "available"
    try:
        _log_and_learn(
            db,
            person_id,
            event_type,
            title,
            "care_event",
            source_id=event.id,
            caregiver_id=caregiver_id,
        )
    except Exception:
        db.rollback()
        learning_pipeline_status = "unavailable"

    # Extraction reads the saved evidence row, not the browser request body.
    natural_language_update = (
        event_type == "daily_care_entry"
        and detail.get("capture_kind") == "natural_language_update"
        and isinstance(detail.get("notes"), str)
    )
    # The extractor must read the caregiver's actual sentence, not the JSON
    # envelope used to persist the complete original capture and its metadata.
    source_text = (
        detail["notes"]
        if natural_language_update
        else evidence.source_text if evidence is not None
        else (event.description or "")
    )[:12_000]
    # Some legacy capture paths populate the event's evidence_ids but leave the
    # local Evidence object unset. Resolve the persisted source before deciding
    # whether background processing can be queued; never fall through to
    # synchronous model inference after the caregiver's save has committed.
    if evidence is None and event.evidence_ids:
        evidence = db.query(EvidenceModel).filter(
            EvidenceModel.person_id == person_id,
            EvidenceModel.id == str(event.evidence_ids[0]),
        ).first()
    if evidence is not None:
        # The caregiver-facing save ends after the event, evidence, analysis
        # placeholder and durable work item commit. Extraction, clarification,
        # Delta reconciliation and projection verification run in the worker.
        execution_status = {
            "source_preservation": "verified",
            "evidence": "verified",
            "extraction": "queued",
            "assessment": "pending",
            "review": "not_required" if natural_language_update else "required",
            "reconciliation": "pending",
            "longitudinal_retrieval": "not_verified",
        }
        try:
            queue_available = (
                inspect(db.get_bind()).has_table("qwen_background_jobs")
                and inspect(db.get_bind()).has_table("qwen_job_events")
            )
        except Exception:
            logger.exception("Could not verify the Daily Care background queue for saved event %s", event.id)
            queue_available = False
        if queue_available:
            try:
                analysis = persist_capture_analysis(
                    db,
                    person_id=person_id,
                    caregiver_id=caregiver_id,
                    source_event_id=event.id,
                    source_evidence_id=evidence.id,
                    source_text=source_text,
                    extraction_status="queued",
                    candidates={"observations": [], "open_questions": []},
                    execution_status=execution_status,
                    commit=False,
                )
                job = QwenBackgroundJob(
                    person_id=person_id,
                    caregiver_id=caregiver_id,
                    task_text="Extract source-grounded observations from the saved Daily Care report.",
                    context_text=json.dumps({
                        "capture_mode": "natural_language_update" if natural_language_update else "structured_capture",
                        "event_type": event_type,
                    }),
                    evidence_ids_json=json.dumps([evidence.id]),
                    workflow="daily_capture_extraction",
                    status="queued",
                    source_event_ids_json=json.dumps([event.id]),
                    source_evidence_ids_json=json.dumps([evidence.id]),
                )
                db.add(job)
                db.flush()
                db.add(QwenJobEvent(
                    job_id=job.id,
                    person_id=person_id,
                    caregiver_id=caregiver_id,
                    event_type="job_queued",
                    from_status=None,
                    to_status="queued",
                    metadata_json={
                        "workflow": "daily_capture_extraction",
                        "source_event_id": event.id,
                        "source_evidence_id": evidence.id,
                        "source_preserved": True,
                        "capture_mode": "natural_language_update" if natural_language_update else "structured_capture",
                        "written_to_lcr": False,
                    },
                ))
                db.commit()
                db.refresh(analysis)
                db.refresh(job)
                return {
                    "success": True,
                    "id": event.id,
                    "change_intelligence_status": "pending",
                    "care_continuity_execution": None,
                    "execution_status": execution_status,
                    "event_type": event.event_type,
                    "recorded_at": event.recorded_at,
                    "evidence_ids": list(event.evidence_ids or []),
                    "idempotent_replay": False if capture_idempotency_key else None,
                    "learning_pipeline_status": learning_pipeline_status,
                    "qwen_extraction_status": "queued",
                    "qwen_candidates": None,
                    "qwen_analysis_id": analysis.id,
                    "qwen_job_id": job.id,
                    "qwen_processing_status": "queued",
                    "qwen_review_required": not natural_language_update,
                    "qwen_written_to_lcr": False,
                }
            except Exception:
                db.rollback()
                logger.exception("Could not queue Daily Care processing for saved event %s", event.id)
        # Never fall back to synchronous model work after a queue failure. The
        # source event/evidence are already committed and must remain usable.
        execution_status.update({
            "extraction": "unavailable",
            "assessment": "not_applicable",
            "reconciliation": "not_run",
        })
        try:
            analysis = persist_capture_analysis(
                db,
                person_id=person_id,
                caregiver_id=caregiver_id,
                source_event_id=event.id,
                source_evidence_id=evidence.id,
                source_text=source_text,
                extraction_status="unavailable",
                candidates={"observations": [], "open_questions": []},
                execution_status=execution_status,
                commit=True,
            )
            analysis_id = analysis.id
        except Exception:
            db.rollback()
            logger.exception("Could not persist unavailable Daily Care processing state for event %s", event.id)
            analysis_id = None
        return {
            "success": True,
            "id": event.id,
            "change_intelligence_status": "unavailable",
            "care_continuity_execution": None,
            "execution_status": execution_status,
            "event_type": event.event_type,
            "recorded_at": event.recorded_at,
            "evidence_ids": list(event.evidence_ids or []),
            "idempotent_replay": False if capture_idempotency_key else None,
            "learning_pipeline_status": learning_pipeline_status,
            "qwen_extraction_status": "unavailable",
            "qwen_candidates": None,
            "qwen_analysis_id": analysis_id,
            "qwen_job_id": None,
            "qwen_processing_status": "unavailable",
            "qwen_review_required": False,
            "qwen_written_to_lcr": False,
        }

    qwen_status = "unavailable"
    qwen_candidates = None
    try:
        qwen_candidates = extract_review_candidates(
            source_text,
            source_event_id=event.id,
            source_evidence_id=evidence.id if evidence is not None else None,
        )
        qwen_status = "available"
    except Exception:
        logger.exception("Daily Care extraction failed after durable source save %s", event.id)

    observations = (
        qwen_candidates.get("observations", [])
        if isinstance(qwen_candidates, dict)
        else []
    )
    if natural_language_update and qwen_status == "available" and evidence is not None:
        try:
            persisted_observation_count = persist_source_grounded_daily_observations(
                db,
                person_id=person_id,
                caregiver_id=caregiver_id,
                evidence_id=evidence.id,
                source_event_id=event.id,
                source_text=source_text,
                candidates=qwen_candidates,
                reported_at=event.recorded_at,
            )
            db.commit()
            for candidate in observations:
                if isinstance(candidate, dict):
                    candidate["review_status"] = "recorded_as_reported_observation"
            assessment_status = (
                "available" if persisted_observation_count
                else "not_applicable"
            )
        except Exception:
            db.rollback()
            assessment_status = "failed"
            logger.exception(
                "Could not persist source-grounded Daily Care observations for event %s",
                event.id,
            )
    else:
        assessment_status = "pending" if qwen_status == "available" else "failed"

    stage_statuses = {
        "extraction": "available" if qwen_status == "available" else "failed",
        "assessment": assessment_status,
        "review": (
            "not_required" if natural_language_update
            else "required" if observations else "not_required"
        ),
    }
    execution_status = {
        "source_preservation": "verified",
        "evidence": "verified" if evidence is not None else "not_applicable",
        **stage_statuses,
        "reconciliation": "pending",
        "longitudinal_retrieval": "not_verified",
    }

    analysis_id = None
    analysis_persisted = False
    try:
        analysis = persist_capture_analysis(
            db,
            person_id=person_id,
            caregiver_id=caregiver_id,
            source_event_id=event.id,
            source_evidence_id=evidence.id if evidence is not None else None,
            source_text=source_text,
            extraction_status=qwen_status,
            candidates=qwen_candidates if qwen_status == "available" else None,
            execution_status=execution_status,
            commit=True,
        )
        analysis_id = analysis.id
        analysis_persisted = True
    except Exception:
        db.rollback()
        logger.exception("Could not persist Daily Care extraction review for event %s", event.id)

    execution = None
    change_intelligence_status = "unavailable"
    try:
        if qwen_status == "available" and isinstance(qwen_candidates, dict):
            persist_source_clarification_questions(
                db,
                person_id=person_id,
                caregiver_id=caregiver_id,
                source_kind="daily_capture_extraction",
                source_record_type="care_event",
                source_record_id=event.id,
                evidence_ids=[evidence.id] if evidence is not None else [],
                questions=(
                    []
                    if natural_language_update
                    else qwen_candidates.get("open_questions") or []
                ),
            )
            # Persist source-linked questions independently of later Delta/
            # change-intelligence work; a reconciliation failure must not erase them.
            db.commit()
        execution = execute_care_continuity_reconciliation(
            db,
            person_id,
            workflow=event_type,
            source_caregiver_id=caregiver_id,
            source_event_id=event.id,
            source_evidence_id=evidence.id if evidence is not None else None,
            stage_statuses=stage_statuses,
        )
        execution_status = dict(execution["stages"])
        execution_status["reconciliation"] = "completed"
        if analysis_persisted and analysis_id:
            persisted_analysis = db.query(CaptureAnalysisModel).filter(
                CaptureAnalysisModel.id == analysis_id,
                CaptureAnalysisModel.person_id == person_id,
                CaptureAnalysisModel.caregiver_id == caregiver_id,
            ).first()
            if persisted_analysis is not None:
                persisted_analysis.execution_status = dict(execution_status)
                db.add(persisted_analysis)
        db.commit()
        execution["stages"]["reconciliation"] = "completed"
        change_intelligence_status = "available"
        try:
            projection = build_lcr_context(
                db,
                person_id,
                caregiver_id=caregiver_id,
                limit_per_source=1,
            )
            retrieval_status = (
                "available"
                if not projection.get("missing_required_sources")
                else "unavailable"
            )
        except Exception:
            retrieval_status = "unavailable"
        execution["stages"]["longitudinal_retrieval"] = retrieval_status
        execution_status["longitudinal_retrieval"] = retrieval_status
        if analysis_persisted and analysis_id:
            try:
                persisted_analysis = db.query(CaptureAnalysisModel).filter(
                    CaptureAnalysisModel.id == analysis_id,
                    CaptureAnalysisModel.person_id == person_id,
                    CaptureAnalysisModel.caregiver_id == caregiver_id,
                ).first()
                if persisted_analysis is not None:
                    persisted_analysis.execution_status = dict(execution_status)
                    db.add(persisted_analysis)
                    db.commit()
            except Exception:
                db.rollback()
                logger.exception("Could not persist retrieval status for event %s", event.id)
    except Exception:
        db.rollback()
        execution_status = {
            "source_preservation": "verified",
            "evidence": "verified" if evidence is not None else "not_applicable",
            **stage_statuses,
            "reconciliation": "failed",
            "longitudinal_retrieval": "unavailable",
        }
        change_intelligence_status = "unavailable"
        logger.exception(
            "Shared care-continuity execution failed after durable capture event %s",
            event.id,
        )
        try:
            failed_analysis = (
                db.query(CaptureAnalysisModel)
                .filter(
                    CaptureAnalysisModel.id == analysis_id,
                    CaptureAnalysisModel.person_id == person_id,
                    CaptureAnalysisModel.caregiver_id == caregiver_id,
                )
                .first()
                if analysis_id else None
            )
            if failed_analysis is not None:
                failed_analysis.execution_status = dict(execution_status)
                db.add(failed_analysis)
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("Could not persist failed execution status for event %s", event.id)

    return {
        "success": True,
        "id": event.id,
        "change_intelligence_status": change_intelligence_status,
        "care_continuity_execution": execution,
        "execution_status": execution_status,
        "event_type": event.event_type,
        "recorded_at": event.recorded_at,
        "evidence_ids": list(event.evidence_ids or []),
        "idempotent_replay": False if capture_idempotency_key else None,
        "learning_pipeline_status": learning_pipeline_status,
        "qwen_extraction_status": qwen_status,
        "qwen_candidates": qwen_candidates,
        "qwen_analysis_id": analysis_id,
        "qwen_review_required": execution_status["review"] == "required",
        "qwen_written_to_lcr": False,
    }


def _validate_quick_check_in(payload: dict) -> None:
    quick = payload.get("quick_check_in")
    if not isinstance(quick, dict) or quick.get("schema_version") != 1:
        raise HTTPException(status_code=422, detail="quick_check_in must use schema version 1")

    kind = quick.get("kind")
    if not isinstance(kind, str) or kind not in {"same_as_usual", "change", "concern", "care_completed", "uncertain"}:
        raise HTTPException(status_code=422, detail="quick_check_in kind is invalid")

    care_areas = {
        "sleep",
        "eating_and_drinking",
        "mobility",
        "mood_and_behavior",
        "communication",
        "daily_routine",
        "personal_care",
    }
    completed_care = {
        "meal_support",
        "personal_care",
        "mobility_support",
        "appointment_support",
        "other_care",
    }
    allowed_areas = completed_care if kind == "care_completed" else care_areas
    selected_areas = quick.get("selected_areas")
    # A check-in is an overall caregiver impression, not a domain-by-domain
    # assessment. All three supported impressions may be recorded without
    # selecting care areas; an empty selection must never imply those areas
    # were assessed or unchanged.
    overall_impression_only = (
        kind in {"same_as_usual", "change", "uncertain"}
        and selected_areas == []
        and quick.get("scope") == "overall_impression_only"
    )
    if not overall_impression_only and (
        not isinstance(selected_areas, list)
        or not selected_areas
        or len(selected_areas) > 7
        or any(not isinstance(area, str) or area not in allowed_areas for area in selected_areas)
        or len(set(selected_areas)) != len(selected_areas)
    ):
        raise HTTPException(status_code=422, detail="quick_check_in must contain valid, unique selected areas")

    if not overall_impression_only and quick.get("scope") != "selected_areas_only":
        raise HTTPException(status_code=422, detail="quick_check_in scope must be selected_areas_only")
    if quick.get("epistemic_status") != "caregiver_reported":
        raise HTTPException(status_code=422, detail="quick_check_in must be marked caregiver_reported")

    note = quick.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 2000):
        raise HTTPException(status_code=422, detail="quick_check_in note must be at most 2000 characters")
    idempotency_key = quick.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
        raise HTTPException(status_code=422, detail="quick_check_in idempotency_key is required and must be at most 128 characters")

    reported_at = quick.get("caregiver_reported_at")
    if not isinstance(reported_at, str) or not reported_at.strip():
        raise HTTPException(status_code=422, detail="quick_check_in caregiver_reported_at is required")
    try:
        parsed_reported_at = datetime.fromisoformat(reported_at.replace("Z", "+00:00"))
        if parsed_reported_at.tzinfo is None or parsed_reported_at.utcoffset() is None:
            raise ValueError("Timezone offset is required")
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="quick_check_in caregiver_reported_at must include a timezone offset")


@router.post("/daily/log")
def create_structured_daily_log(payload: dict, request: Request, db: Session = Depends(get_db)):
    if "quick_check_in" in payload:
        _validate_quick_check_in(payload)
        return _save_daily_capture_event(payload, "quick_daily_checkin", "Quick daily check-in", request, db)
    return _save_daily_capture_event(payload, "structured_daily_capture", "Structured daily capture", request, db)


@router.post("/daily-care/entries")
def create_daily_care_entry(payload: dict, request: Request, db: Session = Depends(get_db)):
    return _save_daily_capture_event(payload, "daily_care_entry", "Daily care entry", request, db)


@router.post("/events", response_model=CareEventSchema)
def create_event(event: CareEventCreate, request: Request, db: Session = Depends(get_db)):
    # This legacy event-ingestion route is still reachable by clients. Bind it
    # to the same server-issued person+caregiver capability as Daily Care.
    _require_session_scope(request, db, event.person_id, event.created_by_caregiver_id)
    _require_person_and_linked_caregiver(
        db, event.person_id, event.created_by_caregiver_id
    )
    db_event = CareEventModel(**event.model_dump())
    db.add(db_event)
    db.commit()
    db.refresh(db_event)

    # The care event is the user-requested write. Learning and reconciliation
    # are secondary work and must not turn a committed event into an HTTP error:
    # clients may otherwise retry and create a duplicate event.
    try:
        _log_and_learn(
            db,
            db_event.person_id,
            "event_recorded",
            f"Recorded {event.event_type}: {event.title}",
            "event",
            source_id=db_event.id,
            caregiver_id=event.created_by_caregiver_id,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Learning pipeline failed after durable care event %s", db_event.id)

    try:
        reconcile_person_change_intelligence(db, db_event.person_id)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Change intelligence reconciliation failed after durable care event %s",
            db_event.id,
        )

    # Refresh after any secondary rollback so response serialization reads the
    # already-committed event, not an expired ORM instance.
    db.refresh(db_event)
    return db_event


@router.get("/events/{person_id}", response_model=List[CareEventSchema])
def list_events(person_id: str, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, person_id)
    return (
        db.query(CareEventModel)
        .filter(CareEventModel.person_id == person_id)
        .order_by(CareEventModel.occurred_at.desc())
        .all()
    )


@router.post("/insights", response_model=InsightSchema)
def create_insight(insight: InsightCreate, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, insight.person_id)
    person = db.query(PersonModel).filter(PersonModel.id == insight.person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if not insight.evidence_ids:
        raise HTTPException(
            status_code=400, detail="Insight must link to at least one evidence item"
        )
    evidence = (
        db.query(EvidenceModel)
        .filter(
            EvidenceModel.id.in_(insight.evidence_ids),
            EvidenceModel.person_id == insight.person_id,
        )
        .all()
    )
    found_ids = {item.id for item in evidence}
    missing_or_foreign_ids = sorted(set(insight.evidence_ids) - found_ids)
    if missing_or_foreign_ids:
        raise HTTPException(
            status_code=422,
            detail="Every evidence item must exist and belong to the same care record",
        )
    db_insight = InsightModel(**insight.model_dump())
    db.add(db_insight)
    db.commit()
    db.refresh(db_insight)
    try:
        _log_and_learn(
            db,
            db_insight.person_id,
            "insight_created",
            f"Created insight: {insight.title}",
            "insight",
            source_id=db_insight.id,
        )
    except Exception:
        # The insight is already committed. Secondary learning must not make
        # the caller believe the write failed and retry it as a duplicate.
        db.rollback()
        logger.exception("Learning pipeline failed after durable insight %s", db_insight.id)
    db.refresh(db_insight)
    return db_insight


@router.get("/insights/{person_id}", response_model=List[InsightSchema])
def list_insights(person_id: str, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, person_id)
    return (
        db.query(InsightModel)
        .filter(InsightModel.person_id == person_id, InsightModel.is_active)
        .order_by(InsightModel.created_at.desc())
        .all()
    )


@router.post("/corrections", response_model=CorrectionSchema)
def create_correction(correction: CorrectionCreate, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, correction.person_id, correction.caregiver_id)
    _require_person_and_linked_caregiver(db, correction.person_id, correction.caregiver_id)

    target_models = {
        "care_event": CareEventModel,
        "care_assertion": CareAssertion,
        "insight": InsightModel,
    }
    target_model = target_models.get(correction.target_type)
    if target_model is None:
        raise HTTPException(status_code=422, detail="This care record type cannot be corrected through this endpoint")
    target = db.query(target_model).filter(
        target_model.id == correction.target_id,
        target_model.person_id == correction.person_id,
    ).first()
    if target is None:
        raise HTTPException(status_code=404, detail="The correction target was not found in this care record")

    db_correction = CorrectionModel(**correction.model_dump())
    db.add(db_correction)
    try:
        db.flush()
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Correction receipt could not be persisted")
        raise HTTPException(
            status_code=503,
            detail="The correction receipt could not be saved. Retry after refreshing the record.",
        ) from exc

    execution = {
        "workflow": "care_record_correction",
        "status": "pending",
        "stages": {"correction": "committed", "reconciliation": "pending"},
        "retry_required": True,
        "failure_code": "reassessment_pending",
    }
    try:
        engine = LearningEngine(db)
        engine.apply_correction(db_correction, commit=False)
        engine.record_learning_event(
            db_correction.person_id,
            "correction_applied",
            f"Corrected {correction.target_type}: {correction.original_text} -> {correction.corrected_text}",
            "correction",
            source_id=db_correction.id,
            caregiver_id=correction.caregiver_id,
            commit=False,
        )
        engine.after_save(db_correction.person_id)
        execution = execute_care_continuity_reconciliation(
            db,
            db_correction.person_id,
            workflow="care_record_correction",
            source_caregiver_id=correction.caregiver_id,
            correction_id=db_correction.id,
        )
        db.commit()
        execution.setdefault("stages", {})["correction"] = "committed"
        execution["stages"]["reconciliation"] = "committed"
        execution["status"] = "committed"
        execution["retry_required"] = False
    except Exception:
        db.rollback()
        logger.exception(
            "Correction receipt %s persisted; care-record reassessment remains pending",
            db_correction.id,
        )
        execution = {
            "workflow": "care_record_correction",
            "status": "pending",
            "stages": {"correction": "committed", "reconciliation": "pending"},
            "retry_required": True,
            "failure_code": "reassessment_pending",
        }

    db.refresh(db_correction)
    db_correction.care_continuity_execution = execution
    return db_correction


@router.get("/corrections/{person_id}", response_model=List[CorrectionSchema])
def list_corrections(person_id: str, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, person_id)
    return (
        db.query(CorrectionModel)
        .filter(CorrectionModel.person_id == person_id)
        .order_by(CorrectionModel.created_at.desc())
        .all()
    )


@router.post("/observations", response_model=ObservationSchema)
def create_observation(observation: ObservationCreate, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, observation.person_id, observation.caregiver_id)
    # Validate referential integrity before persisting an observation.
    person = db.query(PersonModel).filter(PersonModel.id == observation.person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Care recipient not found")

    caregiver = (
        db.query(CaregiverModel)
        .filter(
            CaregiverModel.id == observation.caregiver_id,
            CaregiverModel.person_id == observation.person_id,
        )
        .first()
    )
    if not caregiver:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care record")

    evidence_ids = set(observation.evidence_ids or [])
    if evidence_ids:
        evidence = (
            db.query(EvidenceModel)
            .filter(
                EvidenceModel.id.in_(evidence_ids),
                EvidenceModel.person_id == observation.person_id,
            )
            .all()
        )
        if {item.id for item in evidence} != evidence_ids:
            raise HTTPException(
                status_code=422,
                detail="Evidence is missing or does not belong to this care record",
            )

    db_obs = ObservationModel(**observation.model_dump())
    db.add(db_obs)
    db.commit()
    db.refresh(db_obs)
    _log_and_learn(
        db,
        db_obs.person_id,
        "observation_recorded",
        "Recorded observation",
        "observation",
        source_id=db_obs.id,
        caregiver_id=observation.caregiver_id,
    )
    return db_obs


@router.get("/observations/{person_id}", response_model=List[ObservationSchema])
def list_observations(person_id: str, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, person_id)
    return (
        db.query(ObservationModel)
        .filter(ObservationModel.person_id == person_id)
        .order_by(ObservationModel.observed_at.desc())
        .all()
    )


@router.get("/observations/{person_id}/trends", response_model=List[ObservationTrend])
def get_observation_trends(person_id: str, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, person_id)
    engine = LearningEngine(db)
    return engine.get_observation_trends(person_id)


@router.post("/reasoning-memory", response_model=ReasoningMemorySchema)
def create_reasoning_memory(memory: ReasoningMemoryCreate, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, memory.person_id)
    db_memory = ReasoningMemoryModel(**memory.model_dump())
    db.add(db_memory)
    db.commit()
    db.refresh(db_memory)
    return db_memory


@router.get("/reasoning-memory/{person_id}", response_model=List[ReasoningMemorySchema])
def list_reasoning_memory(person_id: str, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, person_id)
    return (
        db.query(ReasoningMemoryModel)
        .filter(
            ReasoningMemoryModel.person_id == person_id,
            ReasoningMemoryModel.superseded_by_memory_id.is_(None),
        )
        .order_by(ReasoningMemoryModel.created_at.desc())
        .all()
    )


@router.get("/reasoning/{person_id}/summary", response_model=ReasoningSummary)
def get_reasoning_summary(person_id: str, request: Request, db: Session = Depends(get_db)):
    _require_session_scope(request, db, person_id)
    engine = LearningEngine(db)
    return engine.get_reasoning_summary(person_id)


@router.get("/learning-events/{person_id}", response_model=List[LearningEventSchema])
def list_learning_events(person_id: str, request: Request, limit: int = 50, db: Session = Depends(get_db)):
    _require_session_scope(request, db, person_id)
    return (
        db.query(LearningEventModel)
        .filter(LearningEventModel.person_id == person_id)
        .order_by(LearningEventModel.created_at.desc())
        .limit(limit)
        .all()
    )


@router.post("/feedback", response_model=LearningEventSchema)
def record_feedback(
    request: Request,
    person_id: str,
    category: str,
    detail: str,
    caregiver_id: str,
    db: Session = Depends(get_db),
):
    # Feedback is an optional improvement route, not a general unauthenticated
    # write endpoint. Resolve identity from the bearer session and require the
    # caregiver's persisted opt-in before recording or learning from any detail.
    session = _require_session_scope(request, db, person_id, caregiver_id)
    consent = (
        db.query(ConsentRecord)
        .filter_by(person_id=session.person_id, caregiver_id=session.caregiver_id)
        .first()
    )
    consent_verified = bool(
        consent
        and consent.revoked_at is None
        and consent.medical_disclaimer_acknowledged
        and consent.privacy_model_acknowledged
        and consent.multi_caregiver_acknowledged
        and consent.no_advertising_acknowledged
    )
    if not consent_verified or consent.data_improvement_consent is not True:
        raise HTTPException(
            status_code=403,
            detail="Optional improvement feedback requires a verified privacy acknowledgement and an explicit opt-in.",
        )

    engine = LearningEngine(db)
    engine.record_feedback(session.person_id, category, detail, session.caregiver_id)
    engine.after_save(session.person_id)
    # Only explicitly submitted feedback is staged for product-improvement review.
    # Personal learning memory above remains user-scoped regardless of this opt-in.
    stage_consented_feedback(
        db,
        person_id=session.person_id,
        caregiver_id=session.caregiver_id,
        category=category,
        detail=detail,
        consent=consent,
    )
    event = LearningEventModel(
        person_id=session.person_id,
        event_type="feedback",
        detail=f"{category}: {detail}",
        source_type="feedback",
        caregiver_id=session.caregiver_id,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.post("/parse-time", response_model=NaturalLanguageTimeParse)
def parse_time(text: str, timezone: Optional[str] = None):
    result = parse_natural_language_time(text, source_tz=timezone)
    if timezone and validate_timezone(timezone):
        result["timezone_id"] = timezone
    return result


@router.get("/time/display")
def display_time(iso_datetime: str, timezone: Optional[str] = None, format_type: str = "full"):
    dt = datetime.fromisoformat(iso_datetime)
    if format_type == "date":
        result = format_date_local(dt, timezone)
    elif format_type == "time":
        result = format_time_local(dt, timezone)
    else:
        result = format_local(dt, timezone)
    return {"formatted": result, "timezone": timezone}


@router.get("/time/now")
def get_now(timezone: Optional[str] = None):
    now = utc_now()
    return {
        "utc": now.isoformat(),
        "local": format_local(now, timezone),
        "timezone": timezone,
    }


@router.post("/time/ago")
def time_ago(reference_datetime: str, target_datetime: str):
    ref = datetime.fromisoformat(reference_datetime)
    target = datetime.fromisoformat(target_datetime)
    return {"natural": natural_language_time_ago(ref, target)}


@router.post("/time/until")
def time_until(reference_datetime: str, target_datetime: str):
    ref = datetime.fromisoformat(reference_datetime)
    target = datetime.fromisoformat(target_datetime)
    return {"natural": natural_language_time_until(ref, target)}


@router.post("/care-windows", response_model=List[CareWindowSchema])
def create_care_windows_from_event(
    person_id: str, event_type: str, occurred_at: str, db: Session = Depends(get_db)
):
    event_dt = datetime.fromisoformat(occurred_at)
    windows = calculate_care_windows(event_type, event_dt)
    db_windows = []
    for w in windows:
        db_window = CareWindowModel(
            person_id=person_id,
            window_type=w["window_type"],
            started_at=datetime.fromisoformat(w["started_at"]),
            ends_at=datetime.fromisoformat(w["ends_at"]),
            status=w["status"],
            time_provenance=w.get("provenance"),
        )
        db.add(db_window)
        db_windows.append(db_window)
    db.commit()
    for w in db_windows:
        db.refresh(w)
    return db_windows


@router.get("/care-windows/{person_id}", response_model=List[CareWindowSchema])
def list_care_windows(person_id: str, status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(CareWindowModel).filter(CareWindowModel.person_id == person_id)
    if status:
        query = query.filter(CareWindowModel.status == status)
    return query.order_by(CareWindowModel.started_at.desc()).all()


@router.post("/appointments", response_model=AppointmentSchema)
def create_appointment(appointment: AppointmentCreate, db: Session = Depends(get_db)):
    _require_person_and_linked_caregiver(
        db, appointment.person_id, appointment.created_by_caregiver_id
    )
    db_appt = AppointmentModel(**appointment.model_dump())
    db.add(db_appt)
    db.commit()
    db.refresh(db_appt)
    _log_and_learn(
        db,
        db_appt.person_id,
        "appointment_scheduled",
        f"Scheduled: {appointment.title}",
        "appointment",
        source_id=db_appt.id,
        caregiver_id=appointment.created_by_caregiver_id,
    )
    return db_appt


@router.get("/appointments/{person_id}", response_model=List[AppointmentSchema])
def list_appointments(person_id: str, db: Session = Depends(get_db)):
    return (
        db.query(AppointmentModel)
        .filter(AppointmentModel.person_id == person_id)
        .order_by(AppointmentModel.scheduled_at.asc())
        .all()
    )


@router.patch("/appointments/{appointment_id}/lifecycle")
def update_appointment_lifecycle_endpoint(
    appointment_id: str, db: Session = Depends(get_db)
):
    appointment = (
        db.query(AppointmentModel).filter(AppointmentModel.id == appointment_id).first()
    )
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    appt_dict = {
        "id": appointment.id,
        "scheduled_at": appointment.scheduled_at,
        "status": appointment.status,
    }
    new_status = update_appointment_lifecycle(appt_dict)

    if new_status != appointment.status:
        appointment.status = new_status
        if new_status == "completed":
            appointment.completed_at = utc_now()
        elif new_status == "resolved":
            appointment.resolved_at = utc_now()
        db.commit()
        db.refresh(appointment)

    return {"id": appointment.id, "status": appointment.status}


@router.post("/transitions", response_model=CareTransitionSchema)
def create_care_transition(transition: CareTransitionCreate, db: Session = Depends(get_db)):
    _require_person_and_linked_caregiver(
        db, transition.person_id, transition.created_by_caregiver_id, caregiver_required=False
    )
    if not transition.transition_type or transition.transition_type == "unknown":
        inferred = infer_transition_type(transition.from_location, transition.to_location)
        transition.transition_type = inferred

    db_transition = CareTransitionModel(**transition.model_dump())
    db.add(db_transition)
    db.commit()
    db.refresh(db_transition)
    _log_and_learn(
        db,
        db_transition.person_id,
        "transition_recorded",
        f"{transition.from_location or 'Unknown'} -> {transition.to_location}",
        "transition",
        source_id=db_transition.id,
    )
    return db_transition


@router.get("/transitions/{person_id}", response_model=List[CareTransitionSchema])
def list_care_transitions(person_id: str, db: Session = Depends(get_db)):
    return (
        db.query(CareTransitionModel)
        .filter(CareTransitionModel.person_id == person_id)
        .order_by(CareTransitionModel.occurred_at.desc())
        .all()
    )


@router.get("/transitions/{person_id}/sequence/{transition_type}")
def get_care_transition_sequence(person_id: str, transition_type: str, db: Session = Depends(get_db)):
    from app.services.location_intelligence import CARE_TRANSITION_SEQUENCES

    sequence = CARE_TRANSITION_SEQUENCES.get(transition_type, [])
    transitions = (
        db.query(CareTransitionModel)
        .filter(CareTransitionModel.person_id == person_id)
        .order_by(CareTransitionModel.occurred_at.asc())
        .all()
    )

    return {
        "sequence_name": transition_type,
        "known_sequence": sequence,
        "actual_transitions": [
            {
                "from": t.from_location,
                "to": t.to_location,
                "type": t.transition_type,
                "occurred_at": t.occurred_at.isoformat(),
            }
            for t in transitions
        ],
    }


@router.post("/locations", response_model=LocationSchema)
def create_location(location: LocationCreate, db: Session = Depends(get_db)):
    db_location = LocationModel(**location.model_dump())
    db.add(db_location)
    db.commit()
    db.refresh(db_location)
    return db_location


@router.get("/locations/{person_id}", response_model=List[LocationSchema])
def list_locations(person_id: str, db: Session = Depends(get_db)):
    return (
        db.query(LocationModel)
        .filter(LocationModel.person_id == person_id)
        .order_by(LocationModel.is_primary.desc())
        .all()
    )


@router.get("/locations/{person_id}/enriched")
def get_enriched_locations(person_id: str, timezone: Optional[str] = None, db: Session = Depends(get_db)):
    locations = (
        db.query(LocationModel)
        .filter(LocationModel.person_id == person_id)
        .order_by(LocationModel.is_primary.desc())
        .all()
    )
    result = []
    for loc in locations:
        loc_dict = {
            "id": loc.id,
            "name": loc.name,
            "location_type": loc.location_type,
            "city": loc.city,
            "state": loc.state,
            "timezone": loc.timezone,
        }
        enriched = enrich_location_with_geographic_intelligence(loc_dict)
        enriched["display"] = format_location_for_display(loc_dict, timezone)
        result.append(enriched)
    return result


@router.get("/daily-intelligence/{person_id}", response_model=DailyIntelligenceSchema)
def get_daily_intelligence(person_id: str, db: Session = Depends(get_db)):
    events = (
        db.query(CareEventModel)
        .filter(CareEventModel.person_id == person_id)
        .order_by(CareEventModel.occurred_at.desc())
        .limit(50)
        .all()
    )
    appointments = (
        db.query(AppointmentModel)
        .filter(AppointmentModel.person_id == person_id)
        .order_by(AppointmentModel.scheduled_at.asc())
        .all()
    )
    windows = (
        db.query(CareWindowModel)
        .filter(CareWindowModel.person_id == person_id, CareWindowModel.status == "active")
        .all()
    )
    event_dicts = [
        {
            "id": e.id,
            "title": e.title,
            "occurred_at": e.occurred_at.isoformat(),
            "status": e.status,
            "event_type": e.event_type,
        }
        for e in events
    ]
    appointment_dicts = [
        {
            "id": a.id,
            "title": a.title,
            "scheduled_at": a.scheduled_at.isoformat(),
            "location": a.location,
            "status": a.status,
        }
        for a in appointments
    ]
    window_dicts = [
        {
            "id": w.id,
            "window_type": w.window_type,
            "started_at": w.started_at.isoformat(),
            "ends_at": w.ends_at.isoformat(),
            "status": w.status,
        }
        for w in windows
    ]
    result = generate_daily_intelligence(person_id, event_dicts, appointment_dicts, window_dicts)
    db_intel = DailyIntelligenceModel(
        person_id=person_id,
        intelligence_date=datetime.fromisoformat(result["intelligence_date"]),
        overdue_items=result["overdue_items"],
        upcoming_items=result["upcoming_items"],
        active_windows=result["active_windows"],
        expired_windows=result["expired_windows"],
        daily_summary=result["daily_summary"],
    )
    db.add(db_intel)
    db.commit()
    db.refresh(db_intel)
    return db_intel


@router.post("/timezones", response_model=TimezoneContextSchema)
def create_timezone_context(
    person_id: str, caregiver_id: Optional[str] = None, timezone_id: str = "UTC", is_primary: bool = False, db: Session = Depends(get_db)
):
    if not validate_timezone(timezone_id):
        raise HTTPException(status_code=400, detail="Invalid or unsupported timezone")

    tz_context = TimezoneContextModel(
        person_id=person_id,
        caregiver_id=caregiver_id,
        timezone_id=timezone_id,
        is_primary=is_primary,
    )
    db.add(tz_context)
    db.commit()
    db.refresh(tz_context)
    return tz_context


@router.get("/timezones/{person_id}", response_model=List[TimezoneContextSchema])
def list_timezone_contexts(person_id: str, db: Session = Depends(get_db)):
    return (
        db.query(TimezoneContextModel)
        .filter(TimezoneContextModel.person_id == person_id)
        .all()
    )


@router.post("/temporal-relationships", response_model=TemporalRelationshipSchema)
def create_temporal_relationship(
    person_id: str, event_a_id: str, event_b_id: str, db: Session = Depends(get_db)
):
    event_a = (
        db.query(CareEventModel)
        .filter(CareEventModel.id == event_a_id, CareEventModel.person_id == person_id)
        .first()
    )
    event_b = (
        db.query(CareEventModel)
        .filter(CareEventModel.id == event_b_id, CareEventModel.person_id == person_id)
        .first()
    )
    if not event_a or not event_b:
        raise HTTPException(status_code=404, detail="One or both events not found")

    event_a_dict = {
        "id": event_a.id,
        "occurred_at": event_a.occurred_at.isoformat(),
    }
    event_b_dict = {
        "id": event_b.id,
        "occurred_at": event_b.occurred_at.isoformat(),
    }
    relationship = infer_temporal_relationship(event_a_dict, event_b_dict)

    db_rel = TemporalRelationshipModel(
        person_id=person_id,
        event_a_id=event_a_id,
        event_b_id=event_b_id,
        relationship_type=relationship["relationship"],
        time_difference_seconds=str(relationship["time_difference_seconds"]),
        confidence=None,
    )
    db.add(db_rel)
    db.commit()
    db.refresh(db_rel)
    return db_rel


@router.get("/temporal-relationships/{person_id}", response_model=List[TemporalRelationshipSchema])
def list_temporal_relationships(person_id: str, db: Session = Depends(get_db)):
    return (
        db.query(TemporalRelationshipModel)
        .filter(TemporalRelationshipModel.person_id == person_id)
        .order_by(TemporalRelationshipModel.created_at.desc())
        .all()
    )


@router.get("/time-patterns/{person_id}")
def get_time_patterns(person_id: str, window_days: int = 30, db: Session = Depends(get_db)):
    events = (
        db.query(CareEventModel)
        .filter(CareEventModel.person_id == person_id)
        .order_by(CareEventModel.occurred_at.desc())
        .all()
    )
    event_dicts = [
        {
            "id": e.id,
            "title": e.title,
            "occurred_at": e.occurred_at.isoformat(),
            "status": e.status,
            "event_type": e.event_type,
        }
        for e in events
    ]
    patterns = detect_time_patterns(event_dicts, window_days=window_days)
    return {"person_id": person_id, "window_days": window_days, "patterns": patterns}


@router.post("/temporal/facts", response_model=TemporalFactSchema)
def create_temporal_fact_endpoint(fact: TemporalFactCreate, db: Session = Depends(get_db)):
    return create_temporal_fact(db, fact)


@router.patch("/temporal/facts/{fact_id}", response_model=TemporalFactSchema)
def update_temporal_fact_endpoint(fact_id: str, update: TemporalFactUpdate, db: Session = Depends(get_db)):
    result = update_temporal_fact(db, fact_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Temporal fact not found")
    return result


@router.post("/temporal/facts/{fact_id}/supersede")
def supersede_temporal_fact_endpoint(fact_id: str, new_fact_id: str, db: Session = Depends(get_db)):
    result = supersede_temporal_fact(db, fact_id, new_fact_id)
    if not result:
        raise HTTPException(status_code=404, detail="Temporal fact not found")
    return {"id": result.id, "status": result.status.value, "superseded_by_fact_id": result.superseded_by_fact_id}


@router.get("/temporal/facts/{person_id}", response_model=List[TemporalFactSchema])
def list_temporal_facts(person_id: str, subject_type: Optional[str] = None, subject_id: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(TemporalFactModel).filter(TemporalFactModel.person_id == person_id)
    if subject_type:
        query = query.filter(TemporalFactModel.subject_type == subject_type)
    if subject_id:
        query = query.filter(TemporalFactModel.subject_id == subject_id)
    return query.order_by(TemporalFactModel.created_at.desc()).all()


@router.post("/temporal/relations", response_model=TemporalRelationSchema)
def create_temporal_relation_endpoint(relation: TemporalRelationCreate, db: Session = Depends(get_db)):
    return create_temporal_relation(db, relation)


@router.get("/temporal/relations/{person_id}", response_model=List[TemporalRelationSchema])
def list_temporal_relations(person_id: str, db: Session = Depends(get_db)):
    return db.query(TemporalRelationModel).filter(TemporalRelationModel.person_id == person_id).order_by(TemporalRelationModel.created_at.desc()).all()


@router.post("/temporal/reference-frames", response_model=TemporalReferenceFrameSchema)
def create_reference_frame_endpoint(frame: TemporalReferenceFrameCreate, db: Session = Depends(get_db)):
    return create_temporal_reference_frame(db, frame.person_id, frame.name, frame.reference_time, frame.source_event_id, frame.source_fact_id)


@router.get("/temporal/reference-frames/{person_id}", response_model=List[TemporalReferenceFrameSchema])
def list_reference_frames(person_id: str, db: Session = Depends(get_db)):
    return db.query(TemporalReferenceFrameModel).filter(TemporalReferenceFrameModel.person_id == person_id).order_by(TemporalReferenceFrameModel.created_at.desc()).all()


@router.post("/temporal/relative-time", response_model=RelativeTimeResolution)
def resolve_relative_time_endpoint(person_id: str, expression: str, reference_frame_id: Optional[str] = None, db: Session = Depends(get_db)):
    return resolve_relative_time(db, person_id, expression, reference_frame_id)


@router.post("/temporal/infer-relation")
def infer_relation_endpoint(fact_a_id: str, fact_b_id: str, db: Session = Depends(get_db)):
    result = infer_temporal_relation(db, fact_a_id, fact_b_id)
    if not result:
        raise HTTPException(status_code=404, detail="Unable to infer relation")
    return result


@router.get("/temporal/conflicts/{person_id}")
def list_temporal_conflicts(person_id: str, subject_type: Optional[str] = None, subject_id: Optional[str] = None, db: Session = Depends(get_db)):
    return detect_temporal_conflicts(db, person_id, subject_type, subject_id)


@router.post("/temporal/contradictions", response_model=TemporalContradictionSchema)
def create_temporal_contradiction_endpoint(contradiction: TemporalContradictionCreate, db: Session = Depends(get_db)):
    return create_temporal_contradiction(db, contradiction)


@router.patch("/temporal/contradictions/{contradiction_id}", response_model=TemporalContradictionSchema)
def update_temporal_contradiction_endpoint(contradiction_id: str, update: TemporalContradictionUpdate, db: Session = Depends(get_db)):
    result = update_temporal_contradiction(db, contradiction_id, update)
    if not result:
        raise HTTPException(status_code=404, detail="Temporal contradiction not found")
    return result


@router.get("/temporal/check-conflict")
def check_temporal_conflict_endpoint(fact_a_id: str, fact_b_id: str, db: Session = Depends(get_db)):
    result = check_temporal_conflict(db, fact_a_id, fact_b_id)
    if not result:
        return {"has_conflict": False}
    return {"has_conflict": True, **result}


@router.get("/temporal/view/{person_id}", response_model=TemporalViewAtPoint)
def get_temporal_view_endpoint(person_id: str, point_in_time: str, db: Session = Depends(get_db)):
    point = datetime.fromisoformat(point_in_time.replace("Z", "+00:00"))
    return get_temporal_view_at_point(db, person_id, point)


@router.get("/temporal/subject/{person_id}/{subject_type}/{subject_id}", response_model=SubjectTemporalView)
def get_subject_temporal_view_endpoint(person_id: str, subject_type: str, subject_id: str, db: Session = Depends(get_db)):
    return get_subject_temporal_view(db, person_id, subject_type, subject_id)


@router.get("/temporal/duration/{fact_id}")
def get_duration_endpoint(fact_id: str, db: Session = Depends(get_db)):
    result = calculate_duration(db, fact_id)
    if not result:
        raise HTTPException(status_code=404, detail="Temporal fact not found")
    return result


@router.get("/temporal/status/{fact_id}")
def get_temporal_status_endpoint(fact_id: str, reference_time: Optional[str] = None, db: Session = Depends(get_db)):
    fact = db.query(TemporalFactModel).filter(TemporalFactModel.id == fact_id).first()
    if not fact:
        raise HTTPException(status_code=404, detail="Temporal fact not found")
    ref = datetime.fromisoformat(reference_time.replace("Z", "+00:00")) if reference_time else None
    status = classify_temporal_status(fact, ref)
    return {"fact_id": fact_id, "temporal_status": status}




# Only the session-scoped, explicit-approval API is exposed. The legacy
# unscoped action router remains intentionally unmounted.
router.include_router(registered_actions_router)
