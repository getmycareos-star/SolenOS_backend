"""Authenticated, person-scoped API for deep care-continuity records.

The API deliberately reuses existing evidence, events, changes, situations,
temporal and uncertainty tables. It does not create duplicate open-loop or
evidence stores. New records are always scoped from the bearer session, never
from a caller-supplied person_id.
"""
from datetime import datetime, date, timezone
from typing import Any
import json
import logging
import uuid
import hashlib
import re
from difflib import SequenceMatcher

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import MetaData, Table, select, insert, update, desc, inspect, func, or_, DateTime as SQLDateTime, JSON as SQLJSON, Text as SQLText
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.api.care_delta import _scoped_identity
from app.core.caregiver_enums import ClaimType, ObservationType, InformationVisibility
from app.services.lcr_context import build_lcr_context
from app.models.care import Evidence, Caregiver
from app.models.observation_coverage import ObservationCoverage
from app.schemas.observation_coverage import ObservationCoverageCreate
from app.services.observation_coverage import assess_observation_coverage
from pydantic import ValidationError
from app.models.care_continuity import EvidenceReviewRecord, EvidenceClaimAssessment, EvidenceClaimDependency, CareAssertion, EvidenceObservation
from app.schemas.care_continuity import EvidenceClaimAssessmentCreate, EvidenceClaimAssessmentResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/care-continuity", tags=["care-continuity"])


REVIEW_TYPES = {
    "quality": {"adequate", "limited", "insufficient", "undetermined"},
    "reliability": {"supported", "contested", "unverified", "undetermined"},
    "source_confidence": {"verified", "partially_verified", "unverified", "unknown"},
    "freshness": {"current", "stale", "historical", "undetermined"},
    "completeness": {"complete", "partial", "missing", "undetermined"},
}

NEAR_DUPLICATE_ALGORITHM_VERSION = "normalized-text-sequencematcher-v1"
NEAR_DUPLICATE_MIN_CHARS = 80
NEAR_DUPLICATE_RATIO = 0.92
NEAR_DUPLICATE_MAX_RECORDS = 200


def _near_duplicate_candidates(evidence_rows: list[Any]) -> dict[str, Any]:
    """Return conservative text-similarity candidates; never infer shared origin or corroboration."""
    if len(evidence_rows) > NEAR_DUPLICATE_MAX_RECORDS:
        return {
            "status": "not_run_record_limit",
            "algorithm_version": NEAR_DUPLICATE_ALGORITHM_VERSION,
            "candidate_groups": [],
            "records_considered": 0,
            "records_available": len(evidence_rows),
            "interpretation_limit": "Near-duplicate comparison was not run because the linked evidence set exceeds the bounded comparison limit; no conclusion about duplication or independence follows.",
        }

    normalized: dict[str, str] = {}
    for row in evidence_rows:
        source = getattr(row, "source_text", None)
        if not isinstance(source, str):
            continue
        text_value = re.sub(r"\s+", " ", source).strip().casefold()
        if len(text_value) >= NEAR_DUPLICATE_MIN_CHARS:
            normalized[str(row.id)] = text_value

    candidates: list[dict[str, Any]] = []
    ids = sorted(normalized)
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1:]:
            left, right = normalized[left_id], normalized[right_id]
            # Exact normalized duplicates are already reported separately; keep this
            # collection for near-duplicates only to avoid duplicating the same finding.
            if left == right:
                continue
            # Similarity is a candidate-generation heuristic only, not a probability,
            # source-independence assessment, or verification decision.
            if SequenceMatcher(None, left, right, autojunk=False).ratio() >= NEAR_DUPLICATE_RATIO:
                candidates.append({
                    "evidence_ids": [left_id, right_id],
                    "candidate_basis": "normalized_text_similarity",
                    "interpretation": "Text-similarity candidate only; inspect source lineage before deciding whether these records are duplicates or independent.",
                })
    return {
        "status": "completed",
        "algorithm_version": NEAR_DUPLICATE_ALGORITHM_VERSION,
        "candidate_groups": candidates,
        "records_considered": len(normalized),
        "records_available": len(evidence_rows),
        "interpretation_limit": "Text similarity does not establish copying, common origin, factual agreement, or source independence. The threshold is a candidate-generation rule, not a calibrated confidence score.",
    }


REVIEW_POLICY_VERSION = "evidence-review-v1"
REVIEW_CRITERIA = {
    "source attribution": {"present", "incomplete", "unverified", "not_assessed"},
    "independent corroboration": {"independent_source_linked", "copied_or_same_origin", "none_linked", "not_assessed"},
    "temporal context": {"specified", "partial", "missing", "conflicting", "not_assessed"},
    "evidence freshness": {"current", "stale", "historical", "undetermined", "not_assessed"},
    "record completeness": {"complete", "partial", "missing", "undetermined", "not_assessed"},
}


@router.post("/evidence/{evidence_id}/reviews", status_code=201)
def create_evidence_review(evidence_id: str, payload: dict[str, Any], request: Request, db: Session = Depends(get_db)):
    """Append a versioned, evidence-linked review without overwriting source evidence."""
    person_id, caregiver_id = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("evidence_review_records"):
        raise HTTPException(status_code=503, detail="Evidence review storage is not available until schema migration completes")
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
    ).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")

    review_type = str(payload.get("review_type", "")).strip()
    outcome = str(payload.get("outcome", "")).strip()
    rationale = str(payload.get("rationale", "")).strip()
    policy_version = str(payload.get("policy_version", "")).strip()
    criteria = payload.get("criteria")
    if review_type not in REVIEW_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported evidence review type")
    if outcome not in REVIEW_TYPES[review_type]:
        raise HTTPException(status_code=422, detail="Outcome is not valid for this review type")
    if not rationale:
        raise HTTPException(status_code=422, detail="Review rationale is required")
    if policy_version != REVIEW_POLICY_VERSION:
        raise HTTPException(status_code=422, detail=f"Unsupported review policy version; use {REVIEW_POLICY_VERSION}")
    if not isinstance(criteria, list) or len(criteria) != len(REVIEW_CRITERIA):
        raise HTTPException(status_code=422, detail="All versioned evidence review criteria must be supplied exactly once")
    seen_criteria = set()
    for item in criteria:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("criterion", "result", "rationale")
        ):
            raise HTTPException(
                status_code=422,
                detail="Each criterion must include criterion, result, and rationale text",
            )
        criterion = item["criterion"].strip()
        if criterion not in REVIEW_CRITERIA or criterion in seen_criteria:
            raise HTTPException(status_code=422, detail="Unsupported or duplicate evidence review criterion")
        if item["result"].strip() not in REVIEW_CRITERIA[criterion]:
            raise HTTPException(status_code=422, detail=f"Unsupported result for evidence review criterion: {criterion}")
        seen_criteria.add(criterion)
    if seen_criteria != set(REVIEW_CRITERIA):
        raise HTTPException(status_code=422, detail="All versioned evidence review criteria must be supplied")

    attribution = {
        "evidence_id": evidence.id,
        "person_id": evidence.person_id,
        "evidence_type": evidence.type,
        "source_type": evidence.source_type,
        "source_text_present": bool(evidence.source_text),
        "uploaded_at": evidence.uploaded_at.isoformat() if evidence.uploaded_at else None,
        "uploaded_by_caregiver_id": evidence.uploaded_by_caregiver_id,
        "time_provenance": evidence.time_provenance,
        "location": evidence.location,
        "source_span_start": evidence.source_span_start,
        "source_span_end": evidence.source_span_end,
    }
    row = EvidenceReviewRecord(
        evidence_id=evidence.id,
        person_id=person_id,
        review_type=review_type,
        outcome=outcome,
        rationale=rationale,
        criteria=criteria,
        policy_version=policy_version,
        source_attribution_snapshot=json.dumps(attribution, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        reviewed_by_caregiver_id=caregiver_id,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        logger.exception("Evidence review could not be persisted")
        raise HTTPException(status_code=503, detail="Evidence review could not be persisted") from exc
    return {
        "id": row.id,
        "evidence_id": row.evidence_id,
        "person_id": row.person_id,
        "review_type": row.review_type,
        "outcome": row.outcome,
        "rationale": row.rationale,
        "criteria": row.criteria,
        "policy_version": row.policy_version,
        "source_attribution": json.loads(row.source_attribution_snapshot),
        "reviewed_by_caregiver_id": row.reviewed_by_caregiver_id,
        "created_at": row.created_at,
    }


@router.get("/evidence/{evidence_id}/reviews")
def list_evidence_reviews(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("evidence_review_records"):
        raise HTTPException(status_code=503, detail="Evidence review storage is not available until schema migration completes")
    evidence = db.query(Evidence.id).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
    ).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    rows = db.query(EvidenceReviewRecord).filter(
        EvidenceReviewRecord.evidence_id == evidence_id,
        EvidenceReviewRecord.person_id == person_id,
    ).order_by(EvidenceReviewRecord.created_at.asc(), EvidenceReviewRecord.id.asc()).all()
    return [
        {
            "id": row.id,
            "evidence_id": row.evidence_id,
            "person_id": row.person_id,
            "review_type": row.review_type,
            "outcome": row.outcome,
            "rationale": row.rationale,
            "criteria": row.criteria,
            "policy_version": row.policy_version,
            "source_attribution": json.loads(row.source_attribution_snapshot),
            "reviewed_by_caregiver_id": row.reviewed_by_caregiver_id,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/evidence/{evidence_id}/observations", status_code=201)
def create_evidence_observation(
    evidence_id: str,
    payload: dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
):
    """Append a source-span-verified observation to evidence owned by this session."""
    person_id, caregiver_id = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("evidence_observations"):
        raise HTTPException(status_code=503, detail="Structured observation storage is unavailable until migration completes")
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
    ).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")

    allowed = {
        "observation_kind", "epistemic_status", "subject_label", "attribute",
        "value_text", "normalized_value", "units", "context", "occurred_at",
        "observed_at", "reported_at", "observer_label", "observer_caregiver_id",
        "observation_method", "source_passage", "source_span_start", "source_span_end",
        "negated", "hedge_marker", "ambiguity_note", "extraction_method",
        "extraction_version", "extraction_run_id", "supersedes_observation_id",
        "correction_rationale",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unsupported observation fields: {sorted(unknown)}")

    required = ("observation_kind", "epistemic_status", "subject_label", "attribute",
                "source_passage", "source_span_start", "source_span_end",
                "extraction_method", "extraction_version")
    missing = [key for key in required if payload.get(key) is None or (isinstance(payload.get(key), str) and not payload[key].strip())]
    if missing:
        raise HTTPException(status_code=422, detail=f"Required observation fields are missing: {missing}")
    source_text = evidence.source_text
    passage = payload.get("source_passage")
    start, end = payload.get("source_span_start"), payload.get("source_span_end")
    if not isinstance(source_text, str) or not source_text:
        raise HTTPException(status_code=422, detail="Linked evidence has no source text to validate this observation against")
    if isinstance(start, bool) or not isinstance(start, int) or isinstance(end, bool) or not isinstance(end, int):
        raise HTTPException(status_code=422, detail="Source span offsets must be integer character offsets")
    if start < 0 or end <= start or end > len(source_text):
        raise HTTPException(status_code=422, detail="Source span offsets are outside the linked evidence text")
    if source_text[start:end] != passage:
        raise HTTPException(status_code=422, detail="Source passage must exactly match the linked evidence at the supplied character offsets")

    epistemic_status = str(payload["epistemic_status"]).strip().lower()
    allowed_epistemic = {
        "directly_observed", "caregiver_reported", "person_reported",
        "professional_reported", "document_derived", "inferred",
        "uncertain", "disputed", "unknown",
    }
    if epistemic_status not in allowed_epistemic:
        raise HTTPException(status_code=422, detail="Unsupported epistemic status; preserve the source's actual status")

    observer_caregiver_id = payload.get("observer_caregiver_id")
    if observer_caregiver_id and not db.query(Caregiver.id).filter(
        Caregiver.id == observer_caregiver_id, Caregiver.person_id == person_id
    ).first():
        raise HTTPException(status_code=404, detail="Observer caregiver was not found in this care record")

    supersedes_id = payload.get("supersedes_observation_id")
    correction_rationale = payload.get("correction_rationale")
    if bool(supersedes_id) != bool(isinstance(correction_rationale, str) and correction_rationale.strip()):
        raise HTTPException(status_code=422, detail="Corrections require both supersedes_observation_id and correction_rationale")
    if supersedes_id:
        prior = db.query(EvidenceObservation).filter(
            EvidenceObservation.id == supersedes_id,
            EvidenceObservation.person_id == person_id,
            EvidenceObservation.evidence_id == evidence_id,
        ).first()
        if prior is None:
            raise HTTPException(status_code=404, detail="Observation to correct was not found in this evidence record")
        already_superseded = db.query(EvidenceObservation.id).filter(
            EvidenceObservation.person_id == person_id,
            EvidenceObservation.supersedes_observation_id == supersedes_id,
        ).first()
        if already_superseded:
            raise HTTPException(status_code=409, detail="This observation already has a correction; correct the latest version instead")

    def parse_time(field: str):
        value = payload.get(field)
        if value is None:
            return None
        if not isinstance(value, str):
            raise HTTPException(status_code=422, detail=f"{field} must be an ISO-8601 timestamp string")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{field} must be a valid ISO-8601 timestamp") from exc
        return parsed

    row = EvidenceObservation(
        id=str(uuid.uuid4()),
        person_id=person_id,
        evidence_id=evidence_id,
        supersedes_observation_id=supersedes_id,
        correction_rationale=correction_rationale.strip() if isinstance(correction_rationale, str) else None,
        observation_kind=str(payload["observation_kind"]).strip(),
        epistemic_status=epistemic_status,
        subject_label=str(payload["subject_label"]).strip(),
        attribute=str(payload["attribute"]).strip(),
        value_text=payload.get("value_text"),
        normalized_value=payload.get("normalized_value"),
        units=payload.get("units"),
        context=payload.get("context"),
        occurred_at=parse_time("occurred_at"),
        observed_at=parse_time("observed_at"),
        reported_at=parse_time("reported_at"),
        observer_label=payload.get("observer_label"),
        observer_caregiver_id=observer_caregiver_id,
        observation_method=payload.get("observation_method"),
        source_passage=passage,
        source_span_start=start,
        source_span_end=end,
        negated=payload.get("negated"),
        hedge_marker=payload.get("hedge_marker"),
        ambiguity_note=payload.get("ambiguity_note"),
        extraction_method=str(payload["extraction_method"]).strip(),
        extraction_version=str(payload["extraction_version"]).strip(),
        extraction_run_id=payload.get("extraction_run_id"),
        created_by_caregiver_id=caregiver_id,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Source-linked observation could not be persisted")
        raise HTTPException(status_code=503, detail="Source-linked observation could not be persisted") from exc

    return {
        "id": row.id,
        "person_id": row.person_id,
        "evidence_id": row.evidence_id,
        "observation_kind": row.observation_kind,
        "epistemic_status": row.epistemic_status,
        "subject_label": row.subject_label,
        "attribute": row.attribute,
        "value_text": row.value_text,
        "normalized_value": row.normalized_value,
        "source_passage": row.source_passage,
        "source_span_start": row.source_span_start,
        "source_span_end": row.source_span_end,
        "supersedes_observation_id": row.supersedes_observation_id,
        "correction_rationale": row.correction_rationale,
        "created_by_caregiver_id": row.created_by_caregiver_id,
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        "source_of_truth": "persisted_person_scoped_evidence_observation",
        "provenance_validation": "exact_source_text_substring_and_offsets_verified",
    }


@router.get("/evidence/{evidence_id}/integrity")
def get_evidence_source_integrity(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    """Compare the stored source-text hash with the current persisted text, within session scope."""
    person_id, _ = _scoped_identity(request, db)
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
    ).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")

    stored_hash = evidence.source_text_sha256
    computed_hash = (
        hashlib.sha256(evidence.source_text.encode("utf-8")).hexdigest()
        if evidence.source_text is not None
        else None
    )
    if stored_hash is None:
        integrity_status = "unavailable"
    elif computed_hash is None or not __import__("hmac").compare_digest(stored_hash, computed_hash):
        integrity_status = "mismatch"
    else:
        integrity_status = "match"

    def iso(value):
        return value.isoformat() if value is not None else None

    return {
        "evidence_id": evidence.id,
        "person_id": evidence.person_id,
        "source_text_integrity_status": integrity_status,
        "stored_source_text_sha256": stored_hash,
        "recomputed_source_text_sha256": computed_hash,
        "hash_scope": "exact UTF-8 encoding of the currently stored source_text field",
        "original_file_authenticity_verified": False,
        "uploaded_by_caregiver_id": evidence.uploaded_by_caregiver_id,
        "original_author": evidence.original_author,
        "reported_by_label": evidence.reported_by_label,
        "reported_by_caregiver_id": evidence.reported_by_caregiver_id,
        "collected_by_caregiver_id": evidence.collected_by_caregiver_id,
        "collected_at": iso(evidence.collected_at),
        "occurred_at": iso(evidence.occurred_at),
        "occurred_at_timezone": evidence.occurred_at_timezone,
        "uploaded_at": iso(evidence.uploaded_at),
        "ingested_at": iso(evidence.ingested_at),
    }

@router.post(
    "/claims/{claim_id}/assessments",
    response_model=EvidenceClaimAssessmentResponse,
    status_code=201,
)
def create_claim_assessment(
    claim_id: str,
    payload: EvidenceClaimAssessmentCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Append an explicit claim assessment with source passages and server-derived reviewer identity."""
    person_id, caregiver_id = _scoped_identity(request, db)
    claim = db.query(CareAssertion).filter(
        CareAssertion.id == claim_id,
        CareAssertion.person_id == person_id,
    ).first()
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    if not caregiver_id:
        raise HTTPException(status_code=403, detail="A caregiver identity is required to assess a claim")
    if payload.superseded_by_claim_id:
        target_claim = db.query(CareAssertion.id).filter(
            CareAssertion.id == payload.superseded_by_claim_id,
            CareAssertion.person_id == person_id,
        ).first()
        if target_claim is None or payload.superseded_by_claim_id == claim_id:
            raise HTTPException(status_code=422, detail="Supersession target must be a different claim in the same care record")
        # Prevent A -> B -> ... -> A supersession cycles. Only the latest explicit
        # assessment for each claim participates in this directed relationship.
        latest_assessments = {}
        for assessment in (
            db.query(EvidenceClaimAssessment)
            .filter(EvidenceClaimAssessment.person_id == person_id)
            .order_by(EvidenceClaimAssessment.claim_version.asc())
            .all()
        ):
            latest_assessments[assessment.claim_id] = assessment
        cursor = payload.superseded_by_claim_id
        visited = set()
        while cursor and cursor not in visited:
            if cursor == claim_id:
                raise HTTPException(status_code=422, detail="Claim supersession would create a cycle")
            visited.add(cursor)
            next_assessment = latest_assessments.get(cursor)
            cursor = (
                next_assessment.superseded_by_claim_id
                if next_assessment and next_assessment.status == "superseded"
                else None
            )

    links = [item.model_dump() for item in payload.evidence_links]
    evidence_ids = [item["evidence_id"] for item in links]
    if len(set(evidence_ids)) != len(evidence_ids):
        raise HTTPException(status_code=422, detail="Duplicate evidence IDs are not allowed in a claim assessment")

    evidence_rows = {}
    if evidence_ids:
        rows = db.query(Evidence).filter(
            Evidence.person_id == person_id,
            Evidence.id.in_(evidence_ids),
        ).all()
        evidence_rows = {row.id: row for row in rows}
        if set(evidence_rows) != set(evidence_ids):
            raise HTTPException(
                status_code=422,
                detail="Every linked evidence ID must exist in the authenticated care record",
            )

    relationships = {item["relationship"] for item in links}
    required_relationships = {
        "supported": {"supports"},
        "partially_supported": {"supports", "partial_support", "scope_limited"},
        "contradicted": {"contradicts"},
        "conflicting": {"supports", "contradicts"},
    }
    if payload.status in required_relationships:
        required = required_relationships[payload.status]
        if payload.status == "partially_supported":
            valid_support = bool(relationships & required)
        elif payload.status == "conflicting":
            valid_support = "supports" in relationships and "contradicts" in relationships
        else:
            valid_support = bool(relationships & required)
        if payload.status == "supported" and "contradicts" in relationships:
            valid_support = False
        if payload.status == "contradicted" and "supports" in relationships:
            valid_support = False
        if not valid_support:
            raise HTTPException(
                status_code=422,
                detail=f"Claim status {payload.status!r} requires matching explicit evidence relationships and cannot conceal incompatible links",
            )

    for item in links:
        evidence = evidence_rows[item["evidence_id"]]
        passage = item.get("source_passage")
        if passage is not None and passage not in (evidence.source_text or ""):
            raise HTTPException(
                status_code=422,
                detail=f"source_passage must be an exact substring of linked evidence {evidence.id}",
            )

    latest = (
        db.query(EvidenceClaimAssessment)
        .filter(
            EvidenceClaimAssessment.person_id == person_id,
            EvidenceClaimAssessment.claim_id == claim_id,
        )
        .order_by(EvidenceClaimAssessment.claim_version.desc())
        .first()
    )
    row = EvidenceClaimAssessment(
        person_id=person_id,
        claim_id=claim_id,
        claim_version=(latest.claim_version + 1) if latest else 1,
        status=payload.status,
        rationale=payload.rationale.strip(),
        policy_version=payload.policy_version,
        evidence_links=links,
        reviewed_by_caregiver_id=caregiver_id,
        supersedes_assessment_id=latest.id if latest else None,
        superseded_by_claim_id=payload.superseded_by_claim_id,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A concurrent assessment changed this claim version. Reload the assessment history and retry.",
        ) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Claim assessment could not be persisted")
        raise HTTPException(status_code=503, detail="Claim assessment could not be persisted") from exc
    return row


@router.get(
    "/claims/{claim_id}/assessments",
    response_model=list[EvidenceClaimAssessmentResponse],
)
def list_claim_assessments(
    claim_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    person_id, _ = _scoped_identity(request, db)
    claim = db.query(CareAssertion.id).filter(
        CareAssertion.id == claim_id,
        CareAssertion.person_id == person_id,
    ).first()
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return (
        db.query(EvidenceClaimAssessment)
        .filter(
            EvidenceClaimAssessment.person_id == person_id,
            EvidenceClaimAssessment.claim_id == claim_id,
        )
        .order_by(EvidenceClaimAssessment.claim_version.asc())
        .all()
    )


CLAIM_DEPENDENCY_TYPES = {"derived_from", "requires", "supports", "contradicts", "contextualizes"}
CLAIM_DAG_TYPES = {"derived_from", "requires"}


@router.post("/claims/{claim_id}/dependencies", status_code=201)
def create_claim_dependency(
    claim_id: str,
    payload: dict[str, Any],
    request: Request,
    db: Session = Depends(get_db),
):
    """Append a person-scoped claim relationship; dependency edges cannot form cycles."""
    person_id, caregiver_id = _scoped_identity(request, db)
    claim = db.query(CareAssertion).filter(
        CareAssertion.id == claim_id,
        CareAssertion.person_id == person_id,
    ).first()
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    target_id = str(payload.get("depends_on_claim_id") or "").strip()
    relationship_type = str(payload.get("dependency_type") or "").strip().lower()
    rationale = str(payload.get("rationale") or "").strip()
    if not target_id or not rationale:
        raise HTTPException(status_code=422, detail="depends_on_claim_id and a non-empty rationale are required")
    if relationship_type not in CLAIM_DEPENDENCY_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported claim dependency type")
    if target_id == claim_id:
        raise HTTPException(status_code=422, detail="A claim cannot depend on itself")
    if not caregiver_id:
        raise HTTPException(status_code=403, detail="A caregiver identity is required to link claim dependencies")
    target = db.query(CareAssertion.id).filter(
        CareAssertion.id == target_id,
        CareAssertion.person_id == person_id,
    ).first()
    if target is None:
        # Do not disclose whether an ID exists in another person's care record.
        raise HTTPException(status_code=404, detail="Dependency target claim not found")

    if relationship_type in CLAIM_DAG_TYPES:
        edges = db.query(
            EvidenceClaimDependency.claim_id,
            EvidenceClaimDependency.depends_on_claim_id,
        ).filter(
            EvidenceClaimDependency.person_id == person_id,
            EvidenceClaimDependency.dependency_type.in_(CLAIM_DAG_TYPES),
        ).all()
        adjacency: dict[str, list[str]] = {}
        for source_id, dependency_id in edges:
            adjacency.setdefault(str(source_id), []).append(str(dependency_id))
        # Adding claim -> target is cyclic exactly when target already reaches claim.
        pending = [target_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == claim_id:
                raise HTTPException(status_code=422, detail="Claim dependency would create a cycle")
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency.get(current, []))

    row = EvidenceClaimDependency(
        person_id=person_id,
        claim_id=claim_id,
        depends_on_claim_id=target_id,
        dependency_type=relationship_type,
        rationale=rationale,
        created_by_caregiver_id=caregiver_id,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This exact claim relationship already exists") from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Claim dependency could not be persisted")
        raise HTTPException(status_code=503, detail="Claim dependency could not be persisted") from exc
    return {
        "id": row.id,
        "person_id": row.person_id,
        "claim_id": row.claim_id,
        "depends_on_claim_id": row.depends_on_claim_id,
        "dependency_type": row.dependency_type,
        "rationale": row.rationale,
        "created_by_caregiver_id": row.created_by_caregiver_id,
        "created_at": row.created_at,
    }


@router.get("/claims/{claim_id}/dependencies")
def list_claim_dependencies(
    claim_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return incoming and outgoing edges for one claim, without leaking other care records."""
    person_id, _ = _scoped_identity(request, db)
    claim = db.query(CareAssertion.id).filter(
        CareAssertion.id == claim_id,
        CareAssertion.person_id == person_id,
    ).first()
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    rows = db.query(EvidenceClaimDependency).filter(
        EvidenceClaimDependency.person_id == person_id,
        or_(
            EvidenceClaimDependency.claim_id == claim_id,
            EvidenceClaimDependency.depends_on_claim_id == claim_id,
        ),
    ).order_by(EvidenceClaimDependency.created_at.asc(), EvidenceClaimDependency.id.asc()).all()
    return [{
        "id": row.id,
        "person_id": row.person_id,
        "claim_id": row.claim_id,
        "depends_on_claim_id": row.depends_on_claim_id,
        "dependency_type": row.dependency_type,
        "rationale": row.rationale,
        "created_by_caregiver_id": row.created_by_caregiver_id,
        "created_at": row.created_at,
        "direction": "outgoing" if row.claim_id == claim_id else "incoming",
    } for row in rows]


RESOURCES = {
    "facts": "care_facts",
    "fact-evidence": "care_fact_evidence",
    "relationships": "care_record_relationships",
    "delta-items": "care_delta_items",
    "integrity-findings": "care_integrity_findings",
    "integrity-resolutions": "care_integrity_resolutions",
    "decisions": "care_decisions",
    "decision-outcomes": "decision_outcomes",
    "reconciliations": "care_reconciliation_records",
    "follow-up-links": "care_follow_up_links",
    "review-decisions": "care_review_decisions",
    "provenance": "care_provenance_records",
    "timeline": "care_timeline_entries",
    "change-assessments": "care_change_assessments",
    "events": "care_events",
    "evidence": "evidence",
    "changes": "changes",
    "situations": "situations",
    "state-revisions": "care_state_revisions",
    "temporal-facts": "temporal_facts",
    "temporal-relations": "temporal_relations",
    "temporal-relationships": "temporal_relationships",
    "temporal-reference-frames": "temporal_reference_frames",
    "temporal-contradictions": "temporal_contradictions",
    "uncertainty-assessments": "uncertainty_assessments",
    "causal-hypotheses": "causal_hypotheses",
    "causal-evidence": "causal_evidence",
    "contradictions": "contradictions",
    "significance-assessments": "significance_assessments",
    "delta-runs": "care_delta_runs",
    "open-questions": "open_questions",
    "information-gaps": "information_gaps",
    "assertions": "care_assertions",
    "caregiver-contexts": "caregiver_contexts",
    "caregiver-perspectives": "caregiver_perspectives",
    "caregiver-observations": "caregiver_observations",
    "living-records": "living_care_records",
    "record-provenance": "record_provenance",
    "record-provenance-links": "record_provenance_links",
    "record-change-sets": "care_record_change_sets",
    "record-change-items": "care_record_change_items",
    "corrections": "care_corrections",
    "amendments": "record_amendments",
    "amendment-items": "record_amendment_items",
    "record-audit": "care_record_audit_log",
    "uncertainties": "care_uncertainties",
    "missing-information": "care_missing_information",
    "conflicts": "care_conflicts",
    "action-outcomes": "care_action_outcomes",
    "outcomes": "care_action_outcomes",
    "record-identifiers": "care_record_identifiers",
    "record-sections": "care_record_sections",
    "record-links": "care_record_links",
    "record-status-history": "care_record_status_history",
    "fact-revisions": "care_fact_revisions",
    "assertion-revisions": "care_assertion_revisions",
    "decision-revisions": "care_decision_revisions",
    "event-revisions": "care_event_revisions",
    "observation-revisions": "care_observation_revisions",
    "change-revisions": "care_change_revisions",
    "situation-revisions": "care_situation_revisions",
    "evidence-revisions": "care_evidence_revisions",
    "record-retractions": "care_record_retractions",
    "record-restorations": "care_record_restorations",
}

WRITABLE = {
    "facts": {"subject_type", "subject_id", "fact_type", "fact_value", "epistemic_status",
              "effective_at", "superseded_by_fact_id", "review_notes"},
    "assertions": {"subject_type", "subject_id", "field", "assertion_kind", "asserted_value", "source_event_id", "source_label", "source_text", "evidence_ids", "observed_at", "review_after_days"},
    "fact-evidence": {"fact_id", "evidence_id", "evidence_role", "source_span"},
    "relationships": {"source_type", "source_id", "relationship_type", "target_type", "target_id", "provenance"},
    "delta-items": {"delta_run_id", "item_type", "target_type", "target_id", "before_state",
                    "after_state", "evidence_ids", "reconciliation_status", "review_notes"},
    "integrity-findings": {"finding_type", "severity", "status", "target_type", "target_id",
                           "description", "evidence_ids", "detected_by"},
    "integrity-resolutions": {"finding_id", "resolution_status", "resolution_notes", "evidence_ids"},
    "decisions": {"title", "decision_text", "decision_status", "decided_at", "context", "evidence_ids"},
    "decision-outcomes": {"decision_id", "outcome_status", "observed_at", "description",
                          "evidence_ids", "is_intended_outcome"},
    "reconciliations": {"target_type", "target_id", "reconciliation_status", "competing_claim_ids",
                        "evidence_ids", "decision_notes"},
    "follow-up-links": {"source_type", "source_id", "target_type", "target_id", "follow_up_kind", "status", "due_at", "completed_at", "notes", "evidence_ids"},
    "review-decisions": {"target_type", "target_id", "review_decision", "reason", "evidence_ids"},
    "provenance": {"target_type", "target_id", "source_type", "source_id", "evidence_id", "source_label", "source_span", "metadata"},
    "timeline": {"source_type", "source_id", "entry_type", "title", "summary", "occurred_at", "evidence_ids", "epistemic_status", "is_projection"},
    "change-assessments": {"change_id", "assessment_status", "significance", "evidence_status", "assessment_notes", "evidence_ids"},
    # Caregiver-reported context remains attributed perspective, not an LCR fact.
    # Identity fields are injected from the server-issued session below.
    "caregiver-contexts": {"context_type", "context_payload", "epistemic_status", "source_type", "source_id", "recorded_at", "effective_from", "effective_to", "notes"},
    "caregiver-perspectives": {"topic", "perspective_text", "epistemic_status", "evidence_ids", "source_type", "source_id", "recorded_at", "effective_from", "effective_to", "supersedes_id"},
    "caregiver-observations": {"observation_category", "description", "observed_value", "observed_at", "recorded_at", "epistemic_status", "observation_type", "evidence_ids", "source_type", "source_id"},
    "living-records": {"record_status", "schema_version"},
    "record-provenance": {"care_record_id", "source_type", "source_id", "author_label", "method", "recorded_at", "occurred_at", "effective_from", "effective_to", "reason", "metadata"},
    "record-provenance-links": {"provenance_id", "target_type", "target_id", "relationship"},
    "record-change-sets": {"care_record_id", "operation", "status", "reason", "provenance_id"},
    "record-change-items": {"change_set_id", "target_type", "target_id", "operation", "before_value", "after_value", "provenance_id"},
    "corrections": {"care_record_id", "target_type", "target_id", "original_value", "corrected_value", "correction_reason", "status", "provenance_id", "effective_at"},
    "amendments": {"care_record_id", "amendment_type", "reason", "status", "provenance_id"},
    "amendment-items": {"amendment_id", "target_type", "target_id", "field_name", "before_value", "after_value"},
    "record-audit": {"care_record_id", "actor_type", "actor_id", "action", "target_type", "target_id", "outcome", "reason", "metadata", "occurred_at", "request_id"},
    "uncertainties": {"care_record_id", "target_type", "target_id", "question", "uncertainty_type", "status", "source_ids", "resolved_at"},
    "missing-information": {"care_record_id", "topic", "description", "priority", "status", "resolved_at"},
    "conflicts": {"care_record_id", "conflict_type", "status", "claim_refs", "description", "resolution", "resolved_at"},
    "action-outcomes": {"action_id", "action_type", "outcome_status", "notes", "correction_text", "evidence_ids"},
    "outcomes": {"action_id", "action_type", "outcome_status", "notes", "correction_text", "evidence_ids"},
    "record-identifiers": {"care_record_id", "identifier_type", "identifier_fingerprint", "masked_value", "source_type", "source_id", "status", "valid_from", "valid_to"},
    "record-sections": {"care_record_id", "section_key", "section_payload"},
    "record-links": {"source_record_id", "target_record_id", "relationship"},
    "record-retractions": {"care_record_id", "target_type", "target_id", "reason", "provenance_id"},
    "record-restorations": {"care_record_id", "retraction_id", "reason", "provenance_id"},
}

def _table(db: Session, name: str) -> Table:
    try:
        return Table(name, MetaData(), autoload_with=db.get_bind())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Care-continuity schema is not ready: {name}") from exc

JSON_TEXT_FIELDS = {
    "evidence_ids", "source_ids", "source_assertion_ids", "matching_candidate_ids",
    "claim_refs", "competing_claim_ids", "context", "criteria", "metadata",
    "context_payload", "section_payload", "snapshot_payload", "before_state", "after_state",
}


def _jsonable(value: Any):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in JSON_TEXT_FIELDS and isinstance(item, str) and item.lstrip().startswith(("[", "{")):
                try:
                    parsed = json.loads(item)
                    if isinstance(parsed, (list, dict)):
                        item = parsed
                except (TypeError, json.JSONDecodeError):
                    pass
            result[str(key)] = _jsonable(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value

def _validate_links(db: Session, person_id: str, resource: str, payload: dict):
    # Validate cross-table links against the same care recipient to prevent
    # accidentally attaching another person's source material.
    checks = {
        "fact-evidence": [("care_facts", "fact_id"), ("evidence", "evidence_id")],
        "delta-items": [("care_delta_runs", "delta_run_id")],
        "integrity-resolutions": [("care_integrity_findings", "finding_id")],
        "decision-outcomes": [("care_decisions", "decision_id")],
    }
    if resource == "provenance" and payload.get("evidence_id"):
        checks.setdefault(resource, []).append(("evidence", "evidence_id"))
    if resource == "change-assessments":
        checks.setdefault(resource, []).append(("changes", "change_id"))
    for table_name, field in checks.get(resource, []):
        value = payload.get(field)
        if not value:
            raise HTTPException(status_code=422, detail=f"{field} is required")
        table = _table(db, table_name)
        row = db.execute(select(table.c.id).where(table.c.id == value, table.c.person_id == person_id)).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"{field} was not found in this care record")

    # Every explicit evidence reference must resolve within this care record.
    evidence_ids = payload.get("evidence_ids") or []
    if not isinstance(evidence_ids, list):
        raise HTTPException(status_code=422, detail="evidence_ids must be a JSON array")
    if evidence_ids:
        evidence = _table(db, "evidence")
        found = db.execute(
            select(evidence.c.id).where(
                evidence.c.person_id == person_id, evidence.c.id.in_(evidence_ids)
            )
        ).scalars().all()
        if set(map(str, found)) != set(map(str, evidence_ids)):
            raise HTTPException(status_code=404, detail="One or more evidence references are outside this care record")

    typed_tables = {
        "care_fact": "care_facts", "fact": "care_facts",
        "care_decision": "care_decisions", "decision": "care_decisions",
        "care_event": "care_events", "event": "care_events",
        "evidence": "evidence", "change": "changes", "situation": "situations",
        "open_question": "open_questions", "information_gap": "information_gaps",
        "delta_item": "care_delta_items", "integrity_finding": "care_integrity_findings",
        "care_assertion": "care_assertions", "assertion": "care_assertions",
        "care_state_revision": "care_state_revisions", "state_revision": "care_state_revisions",
        "caregiver_context": "caregiver_contexts", "caregiver_perspective": "caregiver_perspectives",
        "caregiver_observation": "caregiver_observations",
        "record_change_set": "care_record_change_sets", "care_record_change_set": "care_record_change_sets",
        "record_change_item": "care_record_change_items", "care_record_change_item": "care_record_change_items",
        "care_correction": "care_corrections", "correction": "care_corrections",
        "record_amendment": "record_amendments", "amendment": "record_amendments",
        "care_uncertainty": "care_uncertainties", "uncertainty": "care_uncertainties",
        "care_conflict": "care_conflicts", "conflict": "care_conflicts",
        "care_action_outcome": "care_action_outcomes", "action_outcome": "care_action_outcomes",
        "living_care_record": "living_care_records", "living_record": "living_care_records",
        "care_record_retraction": "care_record_retractions", "record_retraction": "care_record_retractions",
    }
    links = []
    if resource in ("follow-up-links", "relationships"):
        links.extend([
            (payload.get("source_type"), payload.get("source_id"), "source_id"),
            (payload.get("target_type"), payload.get("target_id"), "target_id"),
        ])
    elif resource in ("review-decisions", "provenance", "record-provenance"):
        if resource in ("review-decisions", "provenance"):
            links.append((payload.get("target_type"), payload.get("target_id"), "target_id"))
        if resource in ("provenance", "record-provenance") and payload.get("source_id"):
            links.append((payload.get("source_type"), payload.get("source_id"), "source_id"))
    elif resource == "timeline":
        links.append((payload.get("source_type"), payload.get("source_id"), "source_id"))
    elif resource in ("caregiver-contexts", "caregiver-perspectives", "caregiver-observations"):
        if payload.get("source_id"):
            links.append((payload.get("source_type"), payload.get("source_id"), "source_id"))
    elif resource in ("record-provenance-links", "record-change-items", "corrections", "amendment-items", "record-audit", "uncertainties", "conflicts", "record-retractions"):
        if payload.get("target_id"):
            links.append((payload.get("target_type"), payload.get("target_id"), "target_id"))
    elif resource == "record-identifiers":
        if payload.get("source_id"):
            links.append((payload.get("source_type"), payload.get("source_id"), "source_id"))
    strict_link_resources = {"record-provenance", "record-provenance-links", "record-change-items", "corrections", "amendment-items", "record-audit", "uncertainties", "conflicts", "record-retractions", "record-identifiers"}
    for type_name, target_id, field_name in links:
        if not target_id:
            continue
        table_name = typed_tables.get(str(type_name or "").lower())
        if table_name is None:
            # Polymorphic IDs are security boundaries. Silently skipping an
            # unknown type lets a caller attach an unverified cross-record ID.
            # Reject unknown linked types for every resource, not just the
            # subset that was previously marked strict.
            raise HTTPException(status_code=422, detail=f"Unsupported {field_name} type for this record")
        linked = _table(db, table_name)
        if "person_id" not in linked.c:
            raise HTTPException(status_code=422, detail=f"{field_name} type is not person-scoped")
        row = db.execute(
            select(linked.c.id).where(linked.c.id == target_id, linked.c.person_id == person_id)
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"{field_name} was not found in this care record")

    # Validate direct foreign-key-like references against the active person's
    # rows before inserts. These are logical links where the DB schema cannot
    # express a polymorphic foreign key.
    scoped_references = {
        "record-change-sets": (("living_care_records", "care_record_id"), ("record_provenance", "provenance_id")),
        "record-change-items": (("care_record_change_sets", "change_set_id"), ("record_provenance", "provenance_id")),
        "record-provenance-links": (("record_provenance", "provenance_id"),),
        "corrections": (("living_care_records", "care_record_id"), ("record_provenance", "provenance_id")),
        "amendments": (("living_care_records", "care_record_id"), ("record_provenance", "provenance_id")),
        "amendment-items": (("record_amendments", "amendment_id"),),
        "record-audit": (("living_care_records", "care_record_id"),),
        "uncertainties": (("living_care_records", "care_record_id"),),
        "missing-information": (("living_care_records", "care_record_id"),),
        "conflicts": (("living_care_records", "care_record_id"),),
        "record-identifiers": (("living_care_records", "care_record_id"),),
        "record-sections": (("living_care_records", "care_record_id"),),
        "record-links": (("living_care_records", "source_record_id"), ("living_care_records", "target_record_id")),
        "record-retractions": (("living_care_records", "care_record_id"), ("record_provenance", "provenance_id")),
        "record-restorations": (("living_care_records", "care_record_id"), ("care_record_retractions", "retraction_id"), ("record_provenance", "provenance_id")),
    }
    for table_name, field in scoped_references.get(resource, ()):
        value = payload.get(field)
        if value is None:
            continue
        linked = _table(db, table_name)
        row = db.execute(
            select(linked.c.id).where(linked.c.id == value, linked.c.person_id == person_id)
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"{field} was not found in this care record")

def _append_revision_for_change_item(db: Session, person_id: str, caregiver_id: str, data: dict):
    target_type = str(data.get("target_type") or "").lower()
    revision_specs = {
        "care_fact": ("care_fact_revisions", "care_fact_id"),
        "fact": ("care_fact_revisions", "care_fact_id"),
        "care_assertion": ("care_assertion_revisions", "assertion_id"),
        "assertion": ("care_assertion_revisions", "assertion_id"),
        "care_decision": ("care_decision_revisions", "decision_id"),
        "decision": ("care_decision_revisions", "decision_id"),
        "care_event": ("care_event_revisions", "event_id"),
        "event": ("care_event_revisions", "event_id"),
        "caregiver_observation": ("care_observation_revisions", "observation_id"),
        "care_observation": ("care_observation_revisions", "observation_id"),
        "observation": ("care_observation_revisions", "observation_id"),
        "care_change": ("care_change_revisions", "change_id"),
        "change": ("care_change_revisions", "change_id"),
        "care_situation": ("care_situation_revisions", "situation_id"),
        "situation": ("care_situation_revisions", "situation_id"),
        "care_evidence": ("care_evidence_revisions", "evidence_id"),
        "evidence": ("care_evidence_revisions", "evidence_id"),
    }
    spec = revision_specs.get(target_type)
    if spec is None:
        raise HTTPException(status_code=422, detail="This target type does not have an LCR revision schema")
    revision_table_name, target_column = spec
    revision_table = _table(db, revision_table_name)
    target_id = data.get("target_id")
    latest = db.execute(
        select(revision_table.c.revision_number)
        .where(revision_table.c[target_column] == target_id, revision_table.c.person_id == person_id)
        .order_by(desc(revision_table.c.revision_number))
        .limit(1)
    ).scalar_one_or_none()
    change_sets = _table(db, "care_record_change_sets")
    change_set = db.execute(
        select(change_sets.c.reason, change_sets.c.provenance_id)
        .where(change_sets.c.id == data.get("change_set_id"), change_sets.c.person_id == person_id)
    ).mappings().first()
    db.execute(insert(revision_table).values(
        id=str(uuid.uuid4()),
        person_id=person_id,
        **{target_column: target_id},
        revision_number=int(latest or 0) + 1,
        previous_payload=data.get("before_value"),
        current_payload=data.get("after_value"),
        revision_reason=(change_set.get("reason") if change_set else None) or data.get("operation"),
        provenance_id=data.get("provenance_id") or (change_set.get("provenance_id") if change_set else None),
        changed_by_caregiver_id=caregiver_id,
        changed_at=datetime.now(timezone.utc),
    ))


@router.get("/context")
def read_lcr_context(
    request: Request,
    limit_per_source: int = Query(default=20, ge=1, le=50),
    query: str | None = Query(default=None, max_length=500),
    db: Session = Depends(get_db),
):
    """Read one bounded, permission-scoped context projection shared by LCR-facing modules."""
    person_id, _ = _scoped_identity(request, db)
    return build_lcr_context(db, person_id, limit_per_source=limit_per_source, query=query)


@router.get("/current-care")
def current_care_projection(
    request: Request,
    limit_per_source: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Return a bounded caregiver-facing projection from existing scoped LCR sources.

    This is a read-only projection, not a new source of truth. It deliberately
    retains competing claims and labels source/status rather than inferring that
    the newest report is necessarily correct.
    """
    person_id, _ = _scoped_identity(request, db)
    context = build_lcr_context(db, person_id, limit_per_source=limit_per_source)
    sources = context.get("sources") or {}
    current_items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(source_name: str, row: dict[str, Any], kind: str, priority: int, *, require_open: bool = False) -> None:
        source_id = str(row.get("id") or "")
        if not source_id or (source_name, source_id) in seen:
            return
        raw_status = row.get("status") or row.get("lifecycle_status") or row.get("reconciliation_status") or row.get("assertion_status") or row.get("decision_status") or row.get("epistemic_status")
        status = str(raw_status).strip().lower() if raw_status is not None else "status_unknown"
        closed_statuses = {"resolved", "completed", "closed", "reconciled", "withdrawn", "retracted", "superseded", "cancelled"}
        if require_open and status in closed_statuses:
            return
        if source_name == "facts" and row.get("superseded_by_fact_id"):
            return
        if source_name == "assertions" and status in {"retracted", "superseded"}:
            return

        title_fields = ("title", "topic", "question", "conflict_type", "fact_type", "field", "attribute", "subject_label", "follow_up_kind", "target_type",
                        "observation_category", "decision_text", "description", "event_type")
        description_fields = ("description", "question", "notes", "fact_value", "asserted_value",
                              "perspective_text", "observed_value", "normalized_value", "value_text", "source_passage", "ambiguity_note", "decision_text", "summary", "resolution", "decision_notes")
        title = next((str(row[k]).strip() for k in title_fields if isinstance(row.get(k), str) and row[k].strip()), kind)
        description = next((str(row[k]).strip() for k in description_fields if isinstance(row.get(k), str) and row[k].strip()), "")
        occurred_at = row.get("occurred_at") or row.get("observed_at") or row.get("effective_at") or row.get("decided_at")
        recorded_at = row.get("recorded_at") or row.get("created_at") or row.get("uploaded_at") or row.get("asked_at")
        evidence_ids = row.get("evidence_ids")
        if not isinstance(evidence_ids, list):
            evidence_ids = row.get("source_ids") if isinstance(row.get("source_ids"), list) else []
        if source_name == "evidence_observations" and row.get("evidence_id"):
            evidence_ids = [str(row["evidence_id"])]
        source_label = row.get("source_label") or row.get("author_label") or row.get("observer_label")
        origin_type = row.get("source_type") or row.get("extraction_method") or "not_recorded"
        claim_refs = row.get("claim_refs") or row.get("competing_claim_ids")
        if isinstance(claim_refs, str):
            try:
                claim_refs = json.loads(claim_refs)
            except (TypeError, json.JSONDecodeError):
                claim_refs = []
        if not isinstance(claim_refs, list):
            claim_refs = []
        current_items.append({
            "id": source_id,
            "kind": kind,
            "priority": priority,
            "title": title[:240],
            "description": description[:1600],
            "status": status,
            "epistemic_status": row.get("epistemic_status") or row.get("epistemic_state") or row.get("assertion_status") or row.get("decision_status") or "not_specified",
            "source_collection": source_name,
            "source_type": str(origin_type)[:80],
            "source_label": str(source_label)[:160] if source_label else "Source label not recorded",
            "occurred_at": occurred_at,
            "due_at": row.get("due_at"),
            "recorded_at": recorded_at,
            "evidence_ids": evidence_ids[:50],
            "claim_refs": claim_refs[:20],
            "claim_count": len(claim_refs),
            "supersedes_observation_id": row.get("supersedes_observation_id") if source_name == "evidence_observations" else None,
            "correction_rationale": row.get("correction_rationale") if source_name == "evidence_observations" else None,
            "source_span_start": row.get("source_span_start") if source_name == "evidence_observations" else None,
            "source_span_end": row.get("source_span_end") if source_name == "evidence_observations" else None,
            "source_passage": row.get("source_passage") if source_name == "evidence_observations" else None,
            "negated": row.get("negated") if source_name == "evidence_observations" else None,
            "hedge_marker": row.get("hedge_marker") if source_name == "evidence_observations" else None,
            "ambiguity_note": row.get("ambiguity_note") if source_name == "evidence_observations" else None,
            "extraction_method": row.get("extraction_method") if source_name == "evidence_observations" else None,
        })
        seen.add((source_name, source_id))

    # Highest priority first: unresolved disagreement, then explicit unknowns,
    # missing information and open questions. These are never auto-resolved.
    for row in (sources.get("reconciliations") or {}).get("items", []):
        add("reconciliations", row, "Reconciliation review", 0, require_open=True)
    for row in (sources.get("conflicts") or {}).get("items", []):
        add("conflicts", row, "Conflict", 0, require_open=True)
    for row in (sources.get("uncertainties") or {}).get("items", []):
        add("uncertainties", row, "Needs clarification", 1, require_open=True)
    for row in (sources.get("information_gaps") or {}).get("items", []):
        add("information_gaps", row, "Information missing", 2, require_open=True)
    for row in (sources.get("missing_information") or {}).get("items", []):
        add("missing_information", row, "Information missing", 2, require_open=True)
    for row in (sources.get("open_questions") or {}).get("items", []):
        add("open_questions", row, "Open question", 3, require_open=True)
    for row in (sources.get("follow_up_links") or {}).get("items", []):
        add("follow_up_links", row, "Follow-up", 4, require_open=True)
    for row in (sources.get("events") or {}).get("items", []):
        event_type = str(row.get("event_type") or "").strip().lower()
        needs_attention = row.get("attention_candidate") is True or row.get("follow_up_candidate") is True
        is_daily_capture = event_type in {"structured_daily_capture", "daily_care_entry"}
        if needs_attention:
            add("events", row, "Care event needing attention", 5, require_open=True)
        elif is_daily_capture:
            # Daily Care is a source event in the longitudinal view even when
            # no automated attention/follow-up flag was raised. It remains a
            # caregiver-recorded event, not a verified clinical fact.
            add("events", row, "Daily care entry", 6)
    for row in (sources.get("caregiver_observations") or {}).get("items", []):
        add("caregiver_observations", row, "Caregiver observation", 6)
    for row in (sources.get("facts") or {}).get("items", []):
        add("facts", row, "Recorded information", 7)
    observation_rows = (sources.get("evidence_observations") or {}).get("items", [])
    superseded_observation_ids = {
        str(row.get("supersedes_observation_id"))
        for row in observation_rows
        if row.get("supersedes_observation_id")
    }
    for row in observation_rows:
        # Current-care view shows the latest non-superseded version. Historical
        # versions remain in the immutable assessment trajectory and source endpoint.
        if str(row.get("id") or "") not in superseded_observation_ids:
            add("evidence_observations", row, "Structured evidence observation", 7)
    for row in (sources.get("assertions") or {}).get("items", []):
        add("assertions", row, "Reported information", 8, require_open=True)
    for row in (sources.get("decisions") or {}).get("items", []):
        add("decisions", row, "Care decision", 9, require_open=True)

    def sort_key(item: dict[str, Any]) -> tuple[int, str]:
        return (int(item["priority"]), str(item.get("recorded_at") or item.get("occurred_at") or ""))

    current_items.sort(key=sort_key, reverse=False)
    # Preserve priority ordering; within each priority, newest recorded item first.
    ordered: list[dict[str, Any]] = []
    for priority in sorted({item["priority"] for item in current_items}):
        group = [item for item in current_items if item["priority"] == priority]
        group.sort(key=lambda item: str(item.get("recorded_at") or item.get("occurred_at") or ""), reverse=True)
        ordered.extend(group)

    # Attach the latest persisted evidence reviews without treating absence of a
    # review as a positive quality or freshness result.
    review_schema_available = inspect(db.get_bind()).has_table("evidence_review_records")
    referenced_evidence_ids = sorted({
        str(evidence_id)
        for item in ordered
        for evidence_id in (item.get("evidence_ids") or [])
        if evidence_id
    })
    latest_reviews: dict[tuple[str, str], EvidenceReviewRecord] = {}
    if review_schema_available and referenced_evidence_ids:
        review_rows = db.query(EvidenceReviewRecord).filter(
            EvidenceReviewRecord.person_id == person_id,
            EvidenceReviewRecord.evidence_id.in_(referenced_evidence_ids),
        ).order_by(EvidenceReviewRecord.created_at.desc(), EvidenceReviewRecord.id.desc()).all()
        for review in review_rows:
            latest_reviews.setdefault((str(review.evidence_id), review.review_type), review)

    for item in ordered:
        evidence_assessments = []
        for evidence_id in item.get("evidence_ids") or []:
            for review_type in ("quality", "reliability", "source_confidence", "freshness", "completeness"):
                review = latest_reviews.get((str(evidence_id), review_type))
                if review is not None:
                    evidence_assessments.append({
                        "evidence_id": str(evidence_id),
                        "review_type": review.review_type,
                        "outcome": review.outcome,
                        "rationale": review.rationale,
                        "policy_version": review.policy_version,
                        "reviewed_at": review.created_at.isoformat() if review.created_at else None,
                    })
        item["evidence_assessments"] = evidence_assessments
        item["evidence_assessment_coverage"] = (
            "persisted_reviews_available" if evidence_assessments
            else ("no_reviews_recorded" if review_schema_available else "unavailable_schema")
        )

    return {
        "person_id": person_id,
        "as_of": context.get("as_of"),
        "projection_type": "bounded_evidence_derived_current_care",
        "read_only": True,
        "history_coverage": context.get("history_coverage", "bounded_subset"),
        "schema_status": context.get("schema_status", "partial"),
        "missing_required_sources": context.get("missing_required_sources", []),
        "missing_optional_sources": context.get("missing_optional_sources", []),
        "items": ordered[:12],
        "item_count": min(len(ordered), 12),
        "has_more": len(ordered) > 12,
        "source_coverage": {
            name: {"status": value.get("status"), "returned_count": value.get("returned_count", 0), "has_more": value.get("has_more", False)}
            for name, value in sources.items()
        },
        "notice": "This is a bounded record view, not a clinical conclusion. Conflicting claims remain separate; unavailable sources may make this view incomplete.",
    }



@router.post("/observation-coverage", status_code=201)
def create_observation_coverage(payload: dict[str, Any], request: Request, db: Session = Depends(get_db)):
    """Persist an explicit observation-coverage interval under the authenticated person's identity."""
    person_id, caregiver_id = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("observation_coverage"):
        raise HTTPException(status_code=503, detail="Observation coverage storage is not available until schema migration completes")

    allowed = {"subject_type", "window_start", "window_end", "coverage_status", "source_evidence_ids", "rationale"}
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unsupported observation-coverage fields: {sorted(unknown)}")
    try:
        validated = ObservationCoverageCreate(person_id=person_id, **payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    evidence_ids = validated.source_evidence_ids
    if len(evidence_ids) != len(set(evidence_ids)):
        raise HTTPException(status_code=422, detail="Duplicate source evidence IDs are not allowed")
    if evidence_ids:
        found = {
            str(row[0]) for row in db.query(Evidence.id).filter(
                Evidence.person_id == person_id,
                Evidence.id.in_(evidence_ids),
            ).all()
        }
        if found != set(evidence_ids):
            raise HTTPException(status_code=422, detail="Every source evidence ID must belong to the authenticated person's care record")

    record = ObservationCoverage(
        id=str(uuid.uuid4()),
        person_id=person_id,
        subject_type=validated.subject_type,
        window_start=validated.window_start,
        window_end=validated.window_end,
        coverage_status=validated.coverage_status,
        source_evidence_ids=evidence_ids,
        rationale=validated.rationale,
        recorded_by_caregiver_id=caregiver_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {
        "id": record.id,
        "person_id": record.person_id,
        "subject_type": record.subject_type,
        "window_start": _jsonable(record.window_start),
        "window_end": _jsonable(record.window_end),
        "coverage_status": record.coverage_status,
        "source_evidence_ids": list(record.source_evidence_ids or []),
        "rationale": record.rationale,
        "recorded_by_caregiver_id": record.recorded_by_caregiver_id,
        "created_at": _jsonable(record.created_at),
    }


@router.get("/observation-coverage")
def list_observation_coverage(
    request: Request,
    subject_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List only the authenticated person's recorded observation-coverage intervals."""
    person_id, _ = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("observation_coverage"):
        raise HTTPException(status_code=503, detail="Observation coverage storage is not available until schema migration completes")
    query = db.query(ObservationCoverage).filter(ObservationCoverage.person_id == person_id)
    if subject_type:
        query = query.filter(ObservationCoverage.subject_type == subject_type)
    rows = query.order_by(ObservationCoverage.window_start.desc(), ObservationCoverage.id.asc()).limit(limit + 1).all()
    return {
        "items": [
            {
                "id": row.id,
                "subject_type": row.subject_type,
                "window_start": _jsonable(row.window_start),
                "window_end": _jsonable(row.window_end),
                "coverage_status": row.coverage_status,
                "source_evidence_ids": list(row.source_evidence_ids or []),
                "rationale": row.rationale,
                "recorded_by_caregiver_id": row.recorded_by_caregiver_id,
                "created_at": _jsonable(row.created_at),
            }
            for row in rows[:limit]
        ],
        "count": min(len(rows), limit),
        "has_more": len(rows) > limit,
        "source_of_truth": "persisted_person_scoped_observation_coverage",
    }


@router.get("/observation-coverage/assessment")
def get_observation_coverage_assessment(
    request: Request,
    subject_type: str = Query(min_length=1, max_length=120),
    window_start: datetime = Query(),
    window_end: datetime = Query(),
    db: Session = Depends(get_db),
):
    """Assess recorded coverage; never infer that no event occurred during an unobserved interval."""
    person_id, _ = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("observation_coverage"):
        raise HTTPException(status_code=503, detail="Observation coverage storage is not available until schema migration completes")
    try:
        result = assess_observation_coverage(db, person_id, subject_type, window_start, window_end)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["records"] = [
        {
            "id": row.id,
            "subject_type": row.subject_type,
            "window_start": _jsonable(row.window_start),
            "window_end": _jsonable(row.window_end),
            "coverage_status": row.coverage_status,
            "source_evidence_ids": list(row.source_evidence_ids or []),
            "rationale": row.rationale,
            "recorded_by_caregiver_id": row.recorded_by_caregiver_id,
            "created_at": _jsonable(row.created_at),
        }
        for row in result["records"]
    ]
    result["window_start"] = _jsonable(result["window_start"])
    result["window_end"] = _jsonable(result["window_end"])
    result["source_of_truth"] = "persisted_person_scoped_observation_coverage"
    return result


def _build_situation_understanding_assessment(db: Session, person_id: str) -> dict[str, Any]:
    """Compose an explainable assessment from persisted LCR sources; never invent care facts."""
    context = build_lcr_context(db, person_id, limit_per_source=50)
    sources = context.get("sources") or {}
    source_domains = {
        "events": "current_state_reconstruction",
        "facts": "current_care_reality",
        "assertions": "care_context_reconstruction",
        "evidence_observations": "evidence_grounded_situation_understanding",
        "caregiver_observations": "functional_status_understanding",
        "caregiver_contexts": "caregiver_capacity_understanding",
        "caregiver_perspectives": "care_needs_understanding",
        "appointments": "care_context_awareness",
        "decision_outcomes": "longitudinal_care_understanding",
        "decisions": "situation_evolution_tracking",
        "uncertainties": "situation_confidence_assessment",
        "uncertainty_assessments": "situation_confidence_assessment",
        "information_gaps": "situation_prioritisation",
        "missing_information": "situation_prioritisation",
        "conflicts": "whole_situation_reasoning",
        "reconciliations": "cross_domain_care_reasoning",
        "provenance": "care_context_reconstruction",
        "timeline": "historical_state_reconstruction",
        "follow_up_links": "situation_reassessment",
    }
    evidence_ids: set[str] = set()
    domain_sources: dict[str, dict[str, Any]] = {}
    all_items: list[dict[str, Any]] = []
    for source_name, source in sources.items():
        rows = source.get("items") or []
        for row in rows:
            normalized_row = dict(row)
            # Structured observations store a single canonical evidence_id, while
            # projections expose a uniform evidence_ids list. Preserve that link
            # in every downstream LCR view instead of losing provenance at the
            # situation-understanding projection boundary.
            if source_name == "evidence_observations" and normalized_row.get("evidence_id"):
                normalized_row["evidence_ids"] = [str(normalized_row["evidence_id"])]
            all_items.append({"source": source_name, **normalized_row})
            ids = row.get("evidence_ids")
            if isinstance(ids, list):
                evidence_ids.update(str(value) for value in ids if value)
            if source_name == "evidence_observations" and row.get("evidence_id"):
                evidence_ids.add(str(row["evidence_id"]))
        domain = source_domains.get(source_name)
        if domain:
            domain_sources.setdefault(domain, {"source_names": [], "record_count": 0, "source_statuses": []})
            domain_sources[domain]["source_names"].append(source_name)
            domain_sources[domain]["record_count"] += len(rows)
            domain_sources[domain]["source_statuses"].append(source.get("status", "unknown"))
    for domain, value in domain_sources.items():
        value["source_names"] = sorted(set(value["source_names"]))
        value["source_statuses"] = sorted(set(value["source_statuses"]))
        value["evidence_state"] = "supported_by_persisted_records" if value["record_count"] else "no_records_returned"
    open_statuses = {"open", "active", "pending", "unresolved", "needs_review", "not_started", "follow_up_pending"}
    closed_statuses = {"resolved", "closed", "completed", "answered", "reconciled", "withdrawn", "retracted", "superseded", "cancelled", "dismissed", "not_applicable", "known"}
    attention_items = []
    uncertainty_items = []
    missing_items = []
    open_loops = []
    baseline_items = []
    open_loop_sources = {
        "conflicts": "conflict_review",
        "reconciliations": "reconciliation_review",
        "uncertainties": "clarification",
        "uncertainty_assessments": "uncertainty_review",
        "information_gaps": "missing_information",
        "missing_information": "missing_information",
        "open_questions": "unanswered_question",
        "follow_up_links": "follow_up",
    }
    for item in all_items:
        status = str(item.get("status") or item.get("lifecycle_status") or item.get("assertion_status") or "").lower()
        source = item["source"]
        assessment_context = item.get("context") if isinstance(item.get("context"), list) else []
        uncertainty_context_entry = next(
            (
                entry for entry in assessment_context
                if isinstance(entry, dict)
                and (
                    any(key in entry for key in ("limitations", "clarification_questions", "evidence_completeness"))
                    or isinstance(entry.get("analysis"), dict)
                )
            ),
            None,
        ) if source == "uncertainty_assessments" else None
        uncertainty_analysis = (
            uncertainty_context_entry.get("analysis")
            if isinstance(uncertainty_context_entry, dict) and isinstance(uncertainty_context_entry.get("analysis"), dict)
            else uncertainty_context_entry
        )
        summary_item = {
            "id": str(item.get("id") or ""),
            "source": source,
            "title": str(item.get("title") or item.get("topic") or item.get("question") or item.get("fact_type") or item.get("field") or item.get("attribute") or item.get("subject_label") or item.get("event_type") or source)[:240],
            "status": status or str(item.get("epistemic_state") or item.get("epistemic_status") or "not_recorded").lower(),
            "epistemic_status": item.get("epistemic_status") or item.get("epistemic_state") or item.get("assertion_status") or "not_specified",
            "occurred_at": item.get("occurred_at") or item.get("observed_at") or item.get("effective_at"),
            "recorded_at": item.get("recorded_at") or item.get("created_at") or item.get("uploaded_at"),
            "evidence_ids": (
                item.get("evidence_ids") if isinstance(item.get("evidence_ids"), list)
                else [str(item["evidence_id"])] if source == "evidence_observations" and item.get("evidence_id")
                else []
            ),
        }
        if source == "evidence_observations":
            summary_item["supersedes_observation_id"] = item.get("supersedes_observation_id")
            summary_item["correction_rationale"] = item.get("correction_rationale")
            summary_item["subject_label"] = item.get("subject_label")
            summary_item["attribute"] = item.get("attribute")
            summary_item["observed_value"] = item.get("normalized_value") or item.get("value_text")
            summary_item["units"] = item.get("units")
            summary_item["source_passage"] = item.get("source_passage")
            summary_item["source_span"] = {
                "start": item.get("source_span_start"),
                "end": item.get("source_span_end"),
            }
            summary_item["negated"] = item.get("negated")
            summary_item["hedge_marker"] = item.get("hedge_marker")
            summary_item["ambiguity_note"] = item.get("ambiguity_note")
            summary_item["extraction_method"] = item.get("extraction_method")
            summary_item["extraction_version"] = item.get("extraction_version")
        if uncertainty_analysis is not None:
            summary_item["uncertainty_analysis"] = uncertainty_analysis
        baseline_marker = (
            item.get("is_baseline") is True
            or item.get("baseline_confirmed") is True
            or str(item.get("record_role") or "").lower() in {"baseline", "usual_state", "usual_pattern"}
            or str(item.get("baseline_type") or "").lower() in {"baseline", "usual_state", "usual_pattern"}
        )
        if baseline_marker:
            baseline_items.append({
                **summary_item,
                "baseline_basis": "explicit_persisted_baseline_marker",
                "interpretation_limit": "A baseline is included only when the source record explicitly marks it as baseline/usual state; no population norm is assumed.",
            })
        if source in {"conflicts", "reconciliations", "uncertainties", "uncertainty_assessments", "information_gaps", "missing_information", "open_questions"} and status not in closed_statuses:
            (uncertainty_items if source in {"conflicts", "reconciliations", "uncertainties", "uncertainty_assessments"} else missing_items).append(summary_item)

        # Open loops are a read-only projection of persisted unresolved items.
        # Do not infer an owner, deadline, clinical result, or completion state.
        if source in open_loop_sources and status not in closed_statuses:
            open_loops.append({
                **summary_item,
                "loop_type": open_loop_sources[source],
                "description": str(item.get("description") or item.get("notes") or item.get("question") or item.get("topic") or "")[:1600],
                "owner": item.get("owner") or item.get("assigned_to") or item.get("owner_id"),
                "due_at": item.get("due_at"),
                "recorded_status": status or "not_recorded",
                "status_confidence": "explicitly_recorded" if status in open_statuses else "unresolved_source_record_status_not_explicit",
            })
        elif source == "events" and (item.get("attention_candidate") is True or item.get("follow_up_candidate") is True):
            attention_items.append(summary_item)
            open_loops.append({
                **summary_item,
                "loop_type": "care_event_follow_up",
                "description": str(item.get("description") or item.get("title") or "")[:1600],
                "owner": item.get("owner") or item.get("assigned_to") or None,
                "due_at": item.get("due_at"),
                "recorded_status": status or "not_recorded",
                "status_confidence": "explicit_follow_up_flag",
            })
        elif source == "follow_up_links" and status in open_statuses:
            attention_items.append(summary_item)
    # Observation coverage is distinct from event evidence: an unobserved interval
    # must never be represented as evidence that an event did not occur.
    coverage_table_available = inspect(db.get_bind()).has_table("observation_coverage")
    coverage_rows = []
    coverage_count = 0
    if coverage_table_available:
        coverage_count = db.query(func.count(ObservationCoverage.id)).filter(
            ObservationCoverage.person_id == person_id
        ).scalar() or 0
        coverage_rows = (
            db.query(ObservationCoverage)
            .filter(ObservationCoverage.person_id == person_id)
            .order_by(desc(ObservationCoverage.window_start), ObservationCoverage.id.asc())
            .limit(100)
            .all()
        )
    observation_coverage = {
        "items": [
            {
                "id": row.id,
                "subject_type": row.subject_type,
                "window_start": row.window_start,
                "window_end": row.window_end,
                "coverage_status": row.coverage_status,
                "source_evidence_ids": list(row.source_evidence_ids or []),
                "rationale": row.rationale,
                "recorded_at": row.created_at,
                "recorded_by_caregiver_id": row.recorded_by_caregiver_id,
                "interpretation_limit": "Observation coverage describes whether a domain was recorded as observed; it does not prove that an event did or did not occur.",
            }
            for row in coverage_rows
        ],
        "count": coverage_count,
        "has_more": coverage_count > len(coverage_rows),
        "status": "available" if coverage_table_available else "unavailable_schema",
        "source_of_truth": "persisted_person_scoped_observation_coverage",
        "absence_rule": "No coverage record means unknown, not no events.",
    }
    capability_specs = {
        "situation_understanding": ["events", "facts", "assertions", "evidence_observations", "caregiver_observations", "caregiver_contexts", "caregiver_perspectives", "decisions", "decision_outcomes", "timeline", "uncertainties", "uncertainty_assessments", "information_gaps", "conflicts"],
        "evidence_grounded_situation_understanding": ["evidence", "evidence_observations", "assertions", "provenance"],
        "current_care_reality": ["events", "facts", "assertions", "evidence_observations", "caregiver_observations", "caregiver_contexts"],
        "care_context_reconstruction": ["assertions", "evidence", "evidence_observations", "provenance", "events", "facts"],
        "whole_situation_reasoning": ["events", "facts", "assertions", "evidence_observations", "caregiver_observations", "caregiver_contexts", "appointments", "conflicts", "reconciliations"],
        "longitudinal_care_understanding": ["timeline", "events", "facts", "decisions", "decision_outcomes"],
        "cross_domain_care_reasoning": ["facts", "evidence_observations", "caregiver_observations", "caregiver_contexts", "reconciliations"],
        "care_context_awareness": ["living_record", "appointments", "caregiver_contexts", "caregiver_perspectives"],
        "functional_status_understanding": ["caregiver_observations", "facts", "assertions", "evidence_observations", "events"],
        "care_needs_understanding": ["information_gaps", "missing_information", "open_questions", "facts", "caregiver_observations"],
        "caregiver_capacity_understanding": ["caregiver_contexts", "caregiver_perspectives", "follow_up_links"],
        "dependency_understanding": ["facts", "caregiver_contexts", "caregiver_observations", "follow_up_links"],
        "environmental_context_understanding": ["caregiver_contexts", "caregiver_observations", "facts", "events"],
        "baseline_establishment": ["facts", "assertions", "evidence_observations", "caregiver_observations", "events"],
        "current_state_reconstruction": ["events", "facts", "assertions", "evidence_observations", "caregiver_observations"],
        "historical_state_reconstruction": ["timeline", "events", "decisions", "decision_outcomes", "corrections"],
        "situation_summarisation": ["events", "facts", "assertions", "evidence_observations", "decisions", "decision_outcomes", "uncertainties", "uncertainty_assessments", "conflicts"],
        "situation_prioritisation": ["conflicts", "reconciliations", "uncertainties", "information_gaps", "missing_information", "open_questions", "follow_up_links", "events"],
        "situation_reassessment": ["follow_up_links", "appointments", "information_gaps", "uncertainties", "events"],
        "situation_evolution_tracking": ["events", "facts", "decisions", "decision_outcomes", "timeline"],
        "situation_confidence_assessment": ["evidence", "evidence_observations", "provenance", "uncertainties", "uncertainty_assessments", "conflicts", "reconciliations", "assertions"],
        "situation_trajectory_reconstruction": ["timeline", "events", "facts", "decisions", "decision_outcomes", "caregiver_observations"],
    }
    capability_specs["observation_coverage_awareness"] = ["observation_coverage"]
    capabilities = {}
    for capability, source_names in capability_specs.items():
        records = sum(len((sources.get(source_name) or {}).get("items") or []) for source_name in source_names)
        unavailable = [source_name for source_name in source_names if (sources.get(source_name) or {}).get("status") == "unavailable_schema"]
        capabilities[capability] = {
            "source_names": source_names,
            "record_count": records,
            "evidence_state": "supported_by_persisted_records" if records else "insufficient_evidence",
            "unavailable_sources": unavailable,
        }
    capabilities["baseline_establishment"] = {
        "source_names": capability_specs["baseline_establishment"],
        "record_count": len(baseline_items),
        "evidence_state": "supported_by_persisted_records" if baseline_items else "insufficient_evidence",
        "unavailable_sources": [
            source_name for source_name in capability_specs["baseline_establishment"]
            if (sources.get(source_name) or {}).get("status") == "unavailable_schema"
        ],
        "interpretation_limit": "Ordinary historical records do not automatically establish an individual baseline.",
    }
    capabilities["observation_coverage_awareness"] = {
        "source_names": ["observation_coverage"],
        "record_count": coverage_count,
        "evidence_state": "supported_by_persisted_records" if coverage_count else "insufficient_evidence",
        "unavailable_sources": [] if coverage_table_available else ["observation_coverage"],
    }
    # A source-linked chronology is not a causal explanation. Preserve event time and
    # record time separately, and leave temporal anchors unknown when not recorded.
    trajectory_sources = {
        "events", "timeline", "facts", "evidence_observations", "caregiver_observations", "caregiver_contexts",
        "caregiver_perspectives", "decisions", "decision_outcomes",
    }
    trajectory_entries = []
    for item in all_items:
        if item["source"] not in trajectory_sources:
            continue
        event_time = item.get("occurred_at") or item.get("observed_at") or item.get("effective_at")
        recorded_time = item.get("recorded_at") or item.get("created_at") or item.get("uploaded_at")
        if not event_time and not recorded_time:
            temporal_basis = "time_not_recorded"
        elif event_time:
            temporal_basis = "event_time_recorded"
        else:
            temporal_basis = "record_time_only"
        trajectory_entries.append({
            "source": item["source"],
            "record_id": str(item.get("id") or ""),
            "title": str(item.get("title") or item.get("topic") or item.get("fact_type") or item.get("field") or item.get("attribute") or item.get("subject_label") or item.get("event_type") or item["source"])[:240],
            "epistemic_status": item.get("epistemic_status") or item.get("epistemic_state") or item.get("assertion_status") or "not_specified",
            "event_time": event_time,
            "recorded_at": recorded_time,
            "temporal_basis": temporal_basis,
            "evidence_ids": (item.get("evidence_ids") if isinstance(item.get("evidence_ids"), list) else [str(item["evidence_id"])] if item["source"] == "evidence_observations" and item.get("evidence_id") else []),
            "source_span": {"start": item.get("source_span_start"), "end": item.get("source_span_end")} if item["source"] == "evidence_observations" else None,
            "supersedes_observation_id": item.get("supersedes_observation_id") if item["source"] == "evidence_observations" else None,
            "correction_rationale": item.get("correction_rationale") if item["source"] == "evidence_observations" else None,
            "subject_label": item.get("subject_label") if item["source"] == "evidence_observations" else None,
            "attribute": item.get("attribute") if item["source"] == "evidence_observations" else None,
            "observed_value": (item.get("normalized_value") or item.get("value_text")) if item["source"] == "evidence_observations" else None,
            "interpretation_limit": "Source-linked chronology only; sequence does not establish causation.",
        })
    for row in coverage_rows:
        trajectory_entries.append({
            "source": "observation_coverage",
            "record_id": str(row.id),
            "title": f"{row.subject_type} observation coverage: {row.coverage_status}",
            "epistemic_status": "explicitly_recorded_coverage",
            "event_time": row.window_start,
            "recorded_at": row.created_at,
            "window_end": row.window_end,
            "coverage_status": row.coverage_status,
            "evidence_ids": list(row.source_evidence_ids or []),
            "temporal_basis": "explicit_observation_window",
            "interpretation_limit": "Observation coverage describes observation opportunity, not whether an event occurred or did not occur.",
        })
    trajectory_entries.sort(
        key=lambda entry: (
            str(entry.get("event_time") or entry.get("recorded_at") or ""),
            str(entry.get("recorded_at") or ""),
            entry["source"],
            entry["record_id"],
        )
    )
    # Report recency metadata without inventing a universal stale threshold. Different
    # care domains have different update cadences; timestamp presence is not freshness proof.
    source_freshness = {}
    def _recency_key(value):
        try:
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return (1, parsed.astimezone(timezone.utc).timestamp())
        except (TypeError, ValueError, OverflowError):
            return (0, str(value))

    for source_name, source in sources.items():
        rows = source.get("items") or []
        recorded_values = [
            (row.get("recorded_at") or row.get("created_at") or row.get("uploaded_at") or row.get("updated_at"))
            for row in rows
        ]
        recorded_values = [value for value in recorded_values if value is not None]
        event_values = [
            (row.get("occurred_at") or row.get("observed_at") or row.get("effective_at"))
            for row in rows
        ]
        event_values = [value for value in event_values if value is not None]
        source_status = source.get("status", "unknown")
        if source_status == "unavailable_schema":
            freshness_status = "unavailable"
        elif not rows:
            freshness_status = "no_records_returned"
        elif recorded_values:
            freshness_status = "record_time_available"
        else:
            freshness_status = "record_time_missing"
        source_freshness[source_name] = {
            "status": freshness_status,
            "record_count": len(rows),
            "records_with_record_time": len(recorded_values),
            "records_with_event_time": len(event_values),
            "latest_recorded_at": max(recorded_values, key=_recency_key) if recorded_values else None,
            "latest_event_at": max(event_values, key=_recency_key) if event_values else None,
            "staleness_assessment": "not_assessed_without_domain_specific_threshold",
            "interpretation_limit": "A timestamp establishes recorded time, not that the information remains current; no universal staleness interval is assumed.",
        }

    required_missing = context.get("missing_required_sources") or []
    optional_missing = context.get("missing_optional_sources") or []
    bounded_sources = sorted(
        source_name for source_name, source in sources.items() if source.get("has_more")
    )
    unlinked_record_count = sum(1 for item in all_items if not item.get("evidence_ids"))
    temporally_unanchored_count = sum(
        1 for entry in trajectory_entries
        if not entry.get("event_time") and not entry.get("recorded_at")
    )
    record_time_only_count = sum(
        1 for entry in trajectory_entries
        if not entry.get("event_time") and entry.get("recorded_at")
    )
    quality_dimensions = [
        {
            "dimension": "source_coverage",
            "status": "insufficient" if required_missing else "partial" if optional_missing or bounded_sources else "available",
            "basis": {
                "required_sources_unavailable": required_missing,
                "optional_sources_unavailable": optional_missing,
                "bounded_sources": bounded_sources,
            },
        },
        {
            "dimension": "evidence_linkage",
            "status": "unknown" if not all_items else "partial" if unlinked_record_count else "linked",
            "basis": {
                "records_examined": len(all_items),
                "records_without_explicit_evidence_link": unlinked_record_count,
                "distinct_linked_evidence_ids": len(evidence_ids),
            },
        },
        {
            "dimension": "temporal_anchoring",
            "status": "unknown" if not trajectory_entries else "partial" if temporally_unanchored_count or record_time_only_count else "event_time_anchored",
            "basis": {
                "trajectory_entries": len(trajectory_entries),
                "entries_without_any_timestamp": temporally_unanchored_count,
                "entries_with_record_time_only": record_time_only_count,
            },
        },
        {
            "dimension": "individual_baseline",
            "status": "explicitly_recorded" if baseline_items else "not_established",
            "basis": {"explicit_baseline_records": len(baseline_items)},
        },
        {
            "dimension": "unresolved_items",
            "status": "requires_review" if open_loops or uncertainty_items or missing_items else "no_open_items_recorded_not_proof_of_absence",
            "basis": {
                "open_loops": len(open_loops),
                "uncertainties_and_conflicts": len(uncertainty_items),
                "missing_information_items": len(missing_items),
            },
        },
    ]
    quality_blockers = []
    if required_missing:
        quality_blockers.append("required_source_unavailable")
    if optional_missing:
        quality_blockers.append("optional_source_unavailable")
    if bounded_sources:
        quality_blockers.append("source_history_bounded")
    if unlinked_record_count:
        quality_blockers.append("some_records_lack_explicit_evidence_links")
    if not baseline_items:
        quality_blockers.append("individual_baseline_not_established")
    if temporally_unanchored_count:
        quality_blockers.append("trajectory_entries_without_any_timestamp")
    if record_time_only_count:
        quality_blockers.append("some_trajectory_entries_have_record_time_only")
    if not trajectory_entries:
        quality_blockers.append("no_attributable_trajectory_entries_returned")
    if open_loops or uncertainty_items or missing_items:
        quality_blockers.append("unresolved_information_requires_review")
    assessment_quality = {
        "status": "limitations_present" if quality_blockers else "source_checks_passed",
        "dimensions": quality_dimensions,
        "blocking_limitations": quality_blockers,
        "high_impact_conclusion_gate": "restricted" if quality_blockers else "source_quality_only; human review and clinical judgement remain outside this scorecard",
        "numeric_confidence": "not_provided; this deterministic scorecard is not a calibrated probability",
        "interpretation_policy": "No-records is not evidence of absence; source availability and record counts do not establish clinical truth.",
    }
    return {
        "schema_version": "situation-understanding.v1",
        "person_id": str(person_id),
        "as_of": context.get("as_of"),
        "assessment_method": "deterministic_composition_of_persisted_lcr_sources",
        "read_only_source_of_truth": True,
        "history_coverage": context.get("history_coverage", "bounded_subset"),
        "schema_status": context.get("schema_status", "partial"),
        "missing_required_sources": required_missing,
        "missing_optional_sources": optional_missing,
        "coverage": {
            "sources_examined": len(sources),
            "sources_with_records": sum(1 for value in sources.values() if value.get("items")),
            "records_examined": len(all_items),
            "distinct_linked_evidence_ids": len(evidence_ids),
            "is_complete": not required_missing and not optional_missing and not any(value.get("has_more") for value in sources.values()),
        },
        "domains": domain_sources,
        "capabilities": capabilities,
        "assessment_quality": assessment_quality,
        "freshness_assessment": {"sources": source_freshness, "policy": "Report recency metadata; do not label records stale without a domain-specific rule."},
        "current_care_reality": {
            "records": [item for item in all_items if item["source"] in {"facts", "events", "assertions", "evidence_observations", "caregiver_observations", "caregiver_contexts"}][:100],
            "notice": "Records are reported with their source status. A record or missing record is not, by itself, proof of clinical truth or absence.",
        },
        "longitudinal_context": {
            "historical_records": [item for item in all_items if item["source"] in {"timeline", "decision_outcomes", "decisions", "evidence_observations"}][:100],
            "bounded": True,
        },
        "baseline_assessment": {
            "status": "explicit_baseline_records_available" if baseline_items else "not_established",
            "items": baseline_items[:100],
            "count": len(baseline_items),
            "has_more": len(baseline_items) > 100,
            "source_of_truth": "explicitly_marked_persisted_lcr_records",
            "limitation": "The earliest available record is not automatically the person's baseline; absence of an explicit baseline leaves baseline unknown.",
        },
        "observation_coverage": observation_coverage,
        "situation_trajectory": {
            "entries": trajectory_entries[:100],
            "count": len(trajectory_entries),
            "has_more": len(trajectory_entries) > 100,
            "ordering": "event_time_then_record_time; missing timestamps remain unanchored",
            "causal_inference": "not_performed",
            "source_of_truth": "persisted_lcr_records",
        },
        "priorities": attention_items[:50],
        "open_loops": {
            "items": open_loops[:100],
            "count": len(open_loops),
            "has_more": len(open_loops) > 100,
            "source_of_truth": "persisted_lcr_records",
            "owner_assignment_policy": "only_explicitly_recorded",
            "deadline_policy": "only_explicitly_recorded",
            "completion_policy": "closed_only_by_persisted_closed_status",
            "notice": "Open loops are unresolved record-derived items, not invented tasks. Unknown owners, due dates, and outcomes remain unknown.",
        },
        "uncertainties_and_conflicts": uncertainty_items[:100],
        "missing_information": missing_items[:100],
        "confidence_assessment": {
            "overall": "not_calibrated",
            "basis": {
                "source_coverage": "partial" if required_missing or optional_missing else "available",
                "linked_evidence_ids": len(evidence_ids),
                "records_without_explicit_evidence_link": sum(1 for item in all_items if not item.get("evidence_ids")),
                "history_coverage": context.get("history_coverage", "bounded_subset"),
            },
            "limitations": ["No calibrated probability is asserted.", "Unavailable or bounded sources can omit relevant context.", "Reported claims and derived assessments are not automatically verified care facts."],
        },
        "notice": "This assessment composes existing persisted LCR sources. It does not diagnose, infer causation, or promote unreviewed claims into authoritative facts.",
    }


@router.get("/situation-understanding")
def get_situation_understanding(request: Request, db: Session = Depends(get_db)):
    """Return the latest persisted assessment for the session-scoped person."""
    person_id, _ = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("care_delta_runs"):
        raise HTTPException(status_code=503, detail="Care-continuity assessment storage is unavailable")
    table = _table(db, "care_delta_runs")
    row = db.execute(
        select(table).where(
            table.c.person_id == person_id,
            table.c.source == "situation_understanding_assessment",
        ).order_by(desc(table.c.created_at)).limit(1)
    ).mappings().first()
    if row is None:
        return {"person_id": person_id, "status": "not_assessed", "assessment": None}
    try:
        assessment = json.loads(row["delta_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Stored situation assessment is invalid") from exc
    return {"person_id": person_id, "status": "available", "assessment_id": row["id"], "created_at": row["created_at"], "assessment": assessment}


@router.get("/situation-understanding/history")
def situation_understanding_history(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Return immutable, person-scoped assessment snapshots newest first."""
    person_id, _ = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("care_delta_runs"):
        raise HTTPException(status_code=503, detail="Care-continuity assessment storage is unavailable")
    table = _table(db, "care_delta_runs")
    rows = db.execute(
        select(table).where(
            table.c.person_id == person_id,
            table.c.source == "situation_understanding_assessment",
        ).order_by(desc(table.c.created_at)).limit(limit)
    ).mappings().all()
    items = []
    for row in rows:
        try:
            assessment = json.loads(row["delta_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail="Stored situation assessment is invalid") from exc
        items.append({
            "assessment_id": row["id"],
            "created_at": _jsonable(row["created_at"]),
            "assessment": assessment,
        })
    return {"person_id": person_id, "items": items, "count": len(items)}


@router.get("/situation-understanding/history/{assessment_id}")
def get_situation_understanding_snapshot(
    assessment_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Read one immutable snapshot only when it belongs to the session-scoped person."""
    person_id, _ = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("care_delta_runs"):
        raise HTTPException(status_code=503, detail="Care-continuity assessment storage is unavailable")
    table = _table(db, "care_delta_runs")
    row = db.execute(
        select(table).where(
            table.c.id == assessment_id,
            table.c.person_id == person_id,
            table.c.source == "situation_understanding_assessment",
        ).limit(1)
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Situation assessment snapshot was not found")
    try:
        assessment = json.loads(row["delta_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Stored situation assessment is invalid") from exc
    return {
        "person_id": person_id,
        "assessment_id": row["id"],
        "created_at": _jsonable(row["created_at"]),
        "assessment": assessment,
    }


@router.post("/situation-understanding/reassess")
def reassess_situation_understanding(request: Request, db: Session = Depends(get_db)):
    """Persist an evidence-derived snapshot; repeated identical assessments are deduplicated."""
    person_id, caregiver_id = _scoped_identity(request, db)
    if not inspect(db.get_bind()).has_table("care_delta_runs"):
        raise HTTPException(status_code=503, detail="Care-continuity assessment storage is unavailable")
    table = _table(db, "care_delta_runs")
    assessment = _build_situation_understanding_assessment(db, person_id)
    latest = db.execute(
        select(table).where(
            table.c.person_id == person_id,
            table.c.source == "situation_understanding_assessment",
        ).order_by(desc(table.c.created_at)).limit(1)
    ).mappings().first()
    if latest:
        try:
            previous_assessment = json.loads(latest.get("delta_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            previous_assessment = {}
        previous_comparison = dict(previous_assessment) if isinstance(previous_assessment, dict) else {}
        current_comparison = dict(assessment)
        # Compare semantic content, excluding the assessment clock.
        previous_comparison.pop("as_of", None)
        current_comparison.pop("as_of", None)
        previous_records = {
            (str(item.get("source", "")), str(item.get("id", ""))): item
            for item in (previous_assessment.get("current_care_reality", {}).get("records", []) if isinstance(previous_assessment, dict) else [])
            if item.get("id")
        }
        current_records = {
            (str(item.get("source", "")), str(item.get("id", ""))): item
            for item in assessment.get("current_care_reality", {}).get("records", [])
            if item.get("id")
        }
        changes = []
        for key, current in current_records.items():
            prior = previous_records.get(key)
            if prior is None:
                changes.append({"change_type": "newly_included", "source": key[0], "record_id": key[1], "current": current, "previous": None})
            elif json.dumps(prior, sort_keys=True, default=str) != json.dumps(current, sort_keys=True, default=str):
                changes.append({"change_type": "record_updated", "source": key[0], "record_id": key[1], "current": current, "previous": prior})
        for key, prior in previous_records.items():
            if key not in current_records:
                changes.append({"change_type": "no_longer_in_current_projection", "source": key[0], "record_id": key[1], "current": None, "previous": prior})
        assessment["evolution_since_previous_assessment"] = {
            "comparison_available": True,
            "previous_assessment_id": latest["id"],
            "change_count": len(changes),
            "changes": changes[:200],
            "interpretation_limit": "Projection differences only. Removal from a bounded projection does not establish deletion or clinical improvement/deterioration.",
        }
        # Avoid a new snapshot when source content and coverage are semantically unchanged.
        previous_comparison.pop("evolution_since_previous_assessment", None)
        current_comparison.pop("evolution_since_previous_assessment", None)
        if previous_comparison == current_comparison and not changes:
            return {"person_id": person_id, "status": "unchanged", "assessment_id": latest["id"], "assessment": previous_assessment}
    else:
        assessment["evolution_since_previous_assessment"] = {
            "comparison_available": False,
            "previous_assessment_id": None,
            "change_count": 0,
            "changes": [],
            "interpretation_limit": "No earlier persisted assessment exists; no historical baseline is assumed.",
        }
    serialized = json.dumps(assessment, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    evidence_ids = sorted({
        str(evidence_id)
        for domain in assessment.get("current_care_reality", {}).get("records", [])
        for evidence_id in domain.get("evidence_ids", [])
        if evidence_id
    })
    now = datetime.now(timezone.utc)
    row = {
        "id": str(uuid.uuid4()),
        "person_id": person_id,
        "source": "situation_understanding_assessment",
        "event_date": now,
        "source_event_id": None,
        # This endpoint uses a reflected SQLAlchemy Table, which sees the
        # JSONList TypeDecorator's TEXT storage as plain TEXT. Serialize explicitly
        # rather than binding a Python list to a TEXT column.
        "evidence_ids": json.dumps(evidence_ids, separators=(",", ":")),
        "delta_json": serialized,
        "model_name": "deterministic-lcr-composition-v1",
        "created_by_caregiver_id": caregiver_id,
        "created_at": now,
    }
    try:
        db.execute(insert(table).values(**row))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("Situation assessment persistence failed for session-scoped person %s", person_id)
        raise HTTPException(status_code=503, detail="Situation assessment could not be persisted") from exc
    return {"person_id": person_id, "status": "created", "assessment_id": row["id"], "assessment": assessment}


@router.get("/summary")
def continuity_summary(request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    result = {"person_id": person_id, "sources": "existing evidence/events/changes/situations are reused",
              "resources": {}}
    # Reflect the inventory in bulk, then count in SQL. The old implementation
    # reflected every table separately and fetched every matching ID into Python,
    # causing the workspace's first care-continuity request to time out on larger
    # inventories. Keep the same keys and session-derived person scope.
    extra_resources = (
        ("open_questions", "open_questions"), ("information_gaps", "information_gaps"),
        ("changes", "changes"), ("situations", "situations"), ("events", "care_events"),
        ("evidence", "evidence"), ("temporal_contradictions", "temporal_contradictions"),
        ("uncertainty_assessments", "uncertainty_assessments"),
        ("significance_assessments", "significance_assessments"), ("delta_runs", "care_delta_runs"),
    )
    resource_to_table = dict(RESOURCES)
    resource_to_table.update(dict(extra_resources))
    table_names = list(dict.fromkeys(resource_to_table.values()))
    bind = db.get_bind()
    available_tables = set(inspect(bind).get_table_names())
    missing_tables = [name for name in table_names if name not in available_tables]
    if missing_tables:
        raise HTTPException(
            status_code=503,
            detail=f"Care-continuity schema is not ready: {missing_tables[0]}",
        )
    metadata = MetaData()
    metadata.reflect(bind=bind, only=table_names)
    for table_name in table_names:
        table = metadata.tables[table_name]
        if "person_id" not in table.c:
            raise HTTPException(
                status_code=503,
                detail=f"Care-continuity schema is not ready: {table_name}",
            )
    count_expressions = [
        select(func.count()).select_from(metadata.tables[table_name])
        .where(metadata.tables[table_name].c.person_id == person_id)
        .scalar_subquery().label(table_name)
        for table_name in table_names
    ]
    counts = dict(db.execute(select(*count_expressions)).mappings().one())
    for resource, table_name in resource_to_table.items():
        result["resources"][resource] = counts[table_name]
    return result

@router.get("/record-versions")
def list_record_versions(request: Request, limit: int = 50, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    versions = _table(db, "care_record_versions")
    records = _table(db, "living_care_records")
    query = (
        select(versions, records.c.person_id.label("person_id"))
        .join(records, versions.c.care_record_id == records.c.id)
        .where(records.c.person_id == person_id)
        .order_by(desc(versions.c.created_at))
    )
    rows = db.execute(query.limit(max(1, min(limit, 100)))).mappings().all()
    return [_jsonable(dict(row)) for row in rows]


@router.get("/record-snapshots")
def list_record_snapshots(request: Request, limit: int = 50, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    snapshots = _table(db, "care_record_snapshots")
    records = _table(db, "living_care_records")
    query = (
        select(snapshots, records.c.person_id.label("person_id"))
        .join(records, snapshots.c.care_record_id == records.c.id)
        .where(records.c.person_id == person_id)
        .order_by(desc(snapshots.c.as_of))
    )
    rows = db.execute(query.limit(max(1, min(limit, 100)))).mappings().all()
    return [_jsonable(dict(row)) for row in rows]


@router.get("/record-revisions")
def list_record_revisions(request: Request, limit: int = 50, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    revision_specs = (
        ("care_fact_revisions", "care_fact_id"),
        ("care_assertion_revisions", "assertion_id"),
        ("care_decision_revisions", "decision_id"),
        ("care_event_revisions", "event_id"),
        ("care_observation_revisions", "observation_id"),
        ("care_change_revisions", "change_id"),
        ("care_situation_revisions", "situation_id"),
        ("care_evidence_revisions", "evidence_id"),
    )
    inspector = inspect(db.get_bind())
    collected = []
    for table_name, target_column in revision_specs:
        if not inspector.has_table(table_name):
            continue
        table = _table(db, table_name)
        rows = db.execute(
            select(table).where(table.c.person_id == person_id)
            .order_by(desc(table.c.changed_at))
            .limit(max(1, min(limit, 100)))
        ).mappings().all()
        for row in rows:
            item = dict(row)
            target_id = item.pop(target_column, None)
            item["target_id"] = target_id
            item["revision_type"] = table_name
            changed_at = item.get("changed_at")
            if isinstance(changed_at, datetime):
                if changed_at.tzinfo is None:
                    changed_at = changed_at.replace(tzinfo=timezone.utc)
                sort_key = changed_at.timestamp()
            else:
                sort_key = 0
            collected.append((sort_key, item))
    collected.sort(key=lambda pair: pair[0], reverse=True)
    return [_jsonable(item) for _, item in collected[:max(1, min(limit, 100))]]


@router.post("/record-versions", status_code=201)
def create_record_version(payload: dict, request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scoped_identity(request, db)
    care_record_id = payload.get("care_record_id")
    if not care_record_id:
        raise HTTPException(status_code=422, detail="care_record_id is required")
    records = _table(db, "living_care_records")
    record = db.execute(
        select(records).where(records.c.id == care_record_id, records.c.person_id == person_id)
    ).mappings().first()
    if record is None:
        raise HTTPException(status_code=404, detail="Living Care Record was not found in this session")

    # Snapshot only canonical persisted rows belonging to the session's person.
    snapshot_tables = (
        "care_facts", "care_assertions", "care_events", "evidence", "changes", "situations",
        "care_state_revisions", "care_delta_runs", "care_delta_items", "care_decisions",
        "decision_outcomes", "care_timeline_entries", "care_follow_up_links",
        "care_review_decisions", "care_provenance_records", "care_uncertainties",
        "care_missing_information", "care_conflicts", "care_action_outcomes",
        "record_provenance", "record_provenance_links", "care_record_change_sets",
        "care_record_change_items", "care_corrections", "record_amendments",
        "record_amendment_items", "care_record_audit_log",
        "care_record_identifiers", "care_record_sections", "care_record_links", "care_record_status_history",
        "care_fact_revisions", "care_assertion_revisions", "care_decision_revisions", "care_event_revisions",
        "care_observation_revisions", "care_change_revisions", "care_situation_revisions", "care_evidence_revisions",
        "care_record_retractions", "care_record_restorations",
        "caregiver_contexts", "caregiver_perspectives", "caregiver_observations",
    )
    tables_payload = {}
    inspector = inspect(db.get_bind())
    for table_name in snapshot_tables:
        if not inspector.has_table(table_name):
            continue
        table = _table(db, table_name)
        if "person_id" not in table.c:
            continue
        query = select(table).where(table.c.person_id == person_id)
        if "created_at" in table.c:
            query = query.order_by(desc(table.c.created_at))
        rows = db.execute(query.limit(1000)).mappings().all()
        tables_payload[table_name] = [_jsonable(dict(row)) for row in rows]

    captured_at = datetime.now(timezone.utc)
    snapshot_payload = {"person_id": person_id, "captured_at": captured_at.isoformat(), "tables": tables_payload}
    encoded = json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    versions = _table(db, "care_record_versions")
    snapshots = _table(db, "care_record_snapshots")
    latest = db.execute(
        select(versions.c.version_number)
        .where(versions.c.care_record_id == care_record_id)
        .order_by(desc(versions.c.version_number))
        .limit(1)
    ).scalar_one_or_none()
    version_id = str(uuid.uuid4())
    snapshot_id = str(uuid.uuid4())
    version_number = int(latest or 0) + 1
    reason = str(payload.get("reason") or "care-continuity snapshot")[:2000]
    try:
        db.execute(insert(versions).values(
            id=version_id, care_record_id=care_record_id, version_number=version_number,
            record_digest=digest, snapshot_payload=snapshot_payload,
            created_by_caregiver_id=caregiver_id, reason=reason,
        ))
        db.execute(insert(snapshots).values(
            id=snapshot_id, care_record_id=care_record_id, version_id=version_id,
            as_of=captured_at, snapshot_payload=snapshot_payload, record_digest=digest,
            created_by_caregiver_id=caregiver_id,
        ))
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Record version could not be committed; refresh and retry") from exc
    return {"id": version_id, "care_record_id": care_record_id, "version_number": version_number,
            "snapshot_id": snapshot_id, "record_digest": digest, "captured_at": captured_at.isoformat(),
            "tables_captured": sorted(tables_payload.keys())}


def _person_scoped_rows(resource: str, person_id: str, request: Request, db: Session, limit: int = 100):
    scoped_person_id, _ = _scoped_identity(request, db)
    if str(person_id) != str(scoped_person_id):
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    table_name = RESOURCES.get(resource)
    if not table_name:
        raise HTTPException(status_code=404, detail="Unknown care-continuity resource")
    table = _table(db, table_name)
    if "person_id" not in table.c:
        raise HTTPException(status_code=422, detail="Resource is not person-scoped")
    query = select(table).where(table.c.person_id == scoped_person_id)
    for key in ("recorded_at", "changed_at", "created_at", "updated_at"):
        if key in table.c:
            query = query.order_by(desc(table.c[key]))
            break
    rows = db.execute(query.limit(max(1, min(limit, 200)))).mappings().all()
    return [_jsonable(dict(row)) for row in rows]


@router.patch("/assertions/{assertion_id}/status")
def review_assertion_status(assertion_id: str, payload: dict[str, Any], request: Request, db: Session = Depends(get_db)):
    person_id, caregiver_id = _scoped_identity(request, db)
    assertions = _table(db, "care_assertions")
    row = db.execute(select(assertions).where(
        assertions.c.id == assertion_id,
        assertions.c.person_id == person_id,
    ).with_for_update()).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Assertion not found")
    previous = _jsonable(dict(row))
    requested_status = str(payload.get("assertion_status") or "").strip()
    if requested_status != "accepted":
        raise HTTPException(status_code=422, detail="This workflow only records an explicit caregiver review as accepted")
    if previous.get("assertion_status") in {"retracted", "superseded"}:
        raise HTTPException(status_code=409, detail="Retracted or superseded assertions cannot be accepted; preserve them and create a new assertion")
    review_days = payload.get("review_after_days", previous.get("review_after_days"))
    if review_days is not None:
        try:
            review_days = int(review_days)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="review_after_days must be a positive integer") from exc
        if review_days < 1 or review_days > 3650:
            raise HTTPException(status_code=422, detail="review_after_days must be between 1 and 3650")
    if previous.get("assertion_kind") == "current_state" and review_days is None:
        raise HTTPException(status_code=422, detail="Current-state assertions require a caregiver-selected review window")
    if previous.get("assertion_status") == "accepted" and previous.get("review_after_days") == review_days:
        return previous

    current = dict(previous)
    current["assertion_status"] = "accepted"
    current["review_after_days"] = review_days
    current["updated_by_caregiver_id"] = caregiver_id
    try:
        values = {"assertion_status": "accepted", "review_after_days": review_days}
        if "updated_by_caregiver_id" in assertions.c:
            values["updated_by_caregiver_id"] = caregiver_id
        if "updated_at" in assertions.c:
            values["updated_at"] = datetime.now(timezone.utc)
        db.execute(update(assertions).where(
            assertions.c.id == assertion_id,
            assertions.c.person_id == person_id,
        ).values(**values))
        if inspect(db.get_bind()).has_table("care_assertion_revisions"):
            revisions = _table(db, "care_assertion_revisions")
            latest = db.execute(select(func.max(revisions.c.revision_number)).where(
                revisions.c.person_id == person_id,
                revisions.c.assertion_id == assertion_id,
            )).scalar()
            db.execute(insert(revisions).values(
                id=str(uuid.uuid4()),
                person_id=person_id,
                assertion_id=assertion_id,
                revision_number=int(latest or 0) + 1,
                previous_payload=previous,
                current_payload=current,
                revision_reason="Caregiver explicitly reviewed the source-attributed assertion",
                changed_by_caregiver_id=caregiver_id,
                changed_at=datetime.now(timezone.utc),
            ))
        db.commit()
        updated = db.execute(select(assertions).where(
            assertions.c.id == assertion_id,
            assertions.c.person_id == person_id,
        )).mappings().one()
        return _jsonable(dict(updated))
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Assertion review could not be persisted")
        raise HTTPException(status_code=503, detail="Assertion review could not be persisted") from exc


@router.get("/assertions/{person_id}")
def list_person_assertions(person_id: str, request: Request, limit: int = 100, db: Session = Depends(get_db)):
    return _person_scoped_rows("assertions", person_id, request, db, limit)


@router.get("/revisions/{person_id}")
def list_person_revisions(person_id: str, request: Request, limit: int = 100, db: Session = Depends(get_db)):
    return _person_scoped_rows("state-revisions", person_id, request, db, limit)


@router.get("/outcomes/{person_id}")
def list_person_outcomes(person_id: str, request: Request, limit: int = 100, db: Session = Depends(get_db)):
    return _person_scoped_rows("action-outcomes", person_id, request, db, limit)


@router.get("/contradictions/{person_id}")
def list_person_contradictions(person_id: str, request: Request, limit: int = 100, db: Session = Depends(get_db)):
    return _person_scoped_rows("contradictions", person_id, request, db, limit)


@router.get("/freshness/{person_id}")
def list_person_assertion_freshness(person_id: str, request: Request, limit: int = 100, db: Session = Depends(get_db)):
    rows = _person_scoped_rows("assertions", person_id, request, db, limit)
    now = datetime.now(timezone.utc)
    output = []
    for row in rows:
        kind = str(row.get("assertion_kind") or "current_state")
        status = str(row.get("assertion_status") or "reported")
        window = row.get("review_after_days")
        timestamp = row.get("recorded_at") or row.get("observed_at")
        age_days = None
        if timestamp:
            try:
                parsed = timestamp if isinstance(timestamp, datetime) else datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age_days = max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds() // 86400))
            except (TypeError, ValueError):
                age_days = None
        if status in {"retracted", "superseded"}:
            freshness_status = "historical"
            explanation = "This assertion is explicitly marked as retracted or superseded in the persisted record."
        elif kind == "time_bound_observation":
            freshness_status = "time_bound_observation"
            explanation = "This is a time-bound observation, not a claim of current state."
        elif age_days is None:
            freshness_status = "undetermined"
            explanation = "The persisted assertion has no parseable recorded time; freshness cannot be determined."
        elif window is None:
            freshness_status = "undetermined"
            explanation = "No caregiver-selected review interval is recorded; freshness is undetermined."
        elif age_days <= int(window):
            freshness_status = "within_review_window"
            explanation = "The assertion is within its explicitly recorded caregiver-selected review window; this does not independently verify the assertion."
        else:
            freshness_status = "needs_confirmation"
            explanation = "The caregiver-selected review interval has elapsed; confirmation is needed before treating this as current."
        output.append({
            "assertion_id": row.get("id"),
            "status": freshness_status,
            "age_days": age_days,
            "review_after_days": window,
            "explanation": explanation,
        })
    return output


@router.post("/next-questions/{person_id}")
def list_persisted_next_questions(person_id: str, request: Request, db: Session = Depends(get_db)):
    scoped_person_id, _ = _scoped_identity(request, db)
    if str(person_id) != str(scoped_person_id):
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    output = []
    if inspect(db.get_bind()).has_table("open_questions"):
        questions = _table(db, "open_questions")
        rows = db.execute(select(questions).where(questions.c.person_id == scoped_person_id)).mappings().all()
        for raw in rows:
            row = _jsonable(dict(raw))
            if str(row.get("status") or "open").casefold() not in {"open", "unanswered", "pending"}:
                continue
            output.append({
                "question": row.get("question") or "Persisted open question",
                "subject_type": row.get("subject_type") or "care_record",
                "subject_id": row.get("subject_id") or scoped_person_id,
                "priority": str(row.get("priority") or "informational"),
                "reason": "This question is persisted as open; no answer is recorded.",
                "evidence_ids": row.get("evidence_ids") if isinstance(row.get("evidence_ids"), list) else [],
                "source_kind": "open_question",
                "source_id": row.get("id"),
                "source_ids": [row.get("id")],
                "persisted_question_id": row.get("id"),
            })
    if inspect(db.get_bind()).has_table("information_gaps"):
        gaps = _table(db, "information_gaps")
        rows = db.execute(select(gaps).where(gaps.c.person_id == scoped_person_id)).mappings().all()
        for raw in rows:
            row = _jsonable(dict(raw))
            if str(row.get("lifecycle_status") or "open").casefold() not in {"open", "unresolved", "pending"}:
                continue
            description = row.get("description") or f"Information gap for {row.get('subject_type') or 'care record'}: {row.get('field') or 'unspecified field'}"
            output.append({
                "question": description,
                "subject_type": row.get("subject_type") or "care_record",
                "subject_id": row.get("subject_id") or scoped_person_id,
                "priority": str(row.get("priority") or "informational"),
                "reason": str(row.get("gap_reason") or "Persisted information gap; it has not been resolved."),
                "evidence_ids": row.get("evidence_ids") if isinstance(row.get("evidence_ids"), list) else [],
                "source_kind": "information_gap",
                "source_id": row.get("id"),
                "source_ids": [row.get("id")],
                "persisted_question_id": None,
            })
    return output


@router.get("/{resource}")
def list_resource(resource: str, request: Request, limit: int = 50, db: Session = Depends(get_db)):
    if resource not in RESOURCES:
        raise HTTPException(status_code=404, detail="Unknown care-continuity resource")
    person_id, _ = _scoped_identity(request, db)
    table = _table(db, RESOURCES[resource])
    query = select(table).where(table.c.person_id == person_id)
    ordering = []
    # Revision sequence is the authoritative order for revisions of a given
    # care item. Timestamps can tie or lose ordering precision across database
    # backends, so use the monotonic revision number before timestamp fallback.
    if "revision_number" in table.c:
        ordering.append(desc(table.c.revision_number))
    if "created_at" in table.c:
        ordering.append(desc(table.c.created_at))
    elif "changed_at" in table.c:
        ordering.append(desc(table.c.changed_at))
    elif "updated_at" in table.c:
        ordering.append(desc(table.c.updated_at))
    if ordering:
        query = query.order_by(*ordering)
    rows = db.execute(query.limit(max(1, min(limit, 100)))).mappings().all()
    return [_jsonable(dict(row)) for row in rows]

@router.get("/facts/{fact_id}/evidence-integrity")
def get_fact_evidence_integrity(fact_id: str, request: Request, db: Session = Depends(get_db)):
    """Return persisted claim links and duplicate-content candidates without claiming independence."""
    person_id, _ = _scoped_identity(request, db)
    facts = _table(db, "care_facts")
    fact = db.execute(
        select(facts.c.id).where(facts.c.id == fact_id, facts.c.person_id == person_id)
    ).first()
    if fact is None:
        raise HTTPException(status_code=404, detail="Claim not found")

    links = _table(db, "care_fact_evidence")
    link_rows = db.execute(
        select(links.c.evidence_id, links.c.evidence_role, links.c.source_span)
        .where(links.c.person_id == person_id, links.c.fact_id == fact_id)
        .order_by(links.c.evidence_id.asc())
    ).all()
    evidence_ids = [str(row.evidence_id) for row in link_rows]
    evidence_rows = db.query(Evidence).filter(
        Evidence.person_id == person_id,
        Evidence.id.in_(evidence_ids or ["__no_evidence__"]),
    ).all()
    evidence_by_id = {str(row.id): row for row in evidence_rows}
    latest_reliability_reviews: dict[str, EvidenceReviewRecord] = {}
    if inspect(db.get_bind()).has_table("evidence_review_records") and evidence_ids:
        review_rows = db.query(EvidenceReviewRecord).filter(
            EvidenceReviewRecord.person_id == person_id,
            EvidenceReviewRecord.evidence_id.in_(evidence_ids),
            EvidenceReviewRecord.review_type == "reliability",
        ).order_by(EvidenceReviewRecord.created_at.desc(), EvidenceReviewRecord.id.desc()).all()
        for review in review_rows:
            latest_reliability_reviews.setdefault(str(review.evidence_id), review)

    groups: dict[str, list[str]] = {}
    fingerprint_reviews: dict[str, set[str]] = {}
    records = []
    for link in link_rows:
        evidence = evidence_by_id.get(str(link.evidence_id))
        if evidence is None:
            records.append({
                "evidence_id": str(link.evidence_id),
                "link_role": link.evidence_role,
                "integrity_status": "linked_evidence_missing",
                "source_independence": "not_established",
            })
            continue
        normalized = re.sub(r"\s+", " ", (evidence.source_text or "").strip().casefold())
        fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None
        if fingerprint:
            ids_for_fingerprint = groups.setdefault(fingerprint, [])
            if str(evidence.id) not in ids_for_fingerprint:
                ids_for_fingerprint.append(str(evidence.id))
        review = latest_reliability_reviews.get(str(evidence.id))
        independence_review = "not_reviewed"
        if review is not None:
            for criterion in review.criteria or []:
                if isinstance(criterion, dict) and criterion.get("criterion") == "independent corroboration":
                    independence_review = str(criterion.get("result") or "not_assessed")
                    break
        if fingerprint:
            fingerprint_reviews.setdefault(fingerprint, set()).add(independence_review)
        records.append({
            "evidence_id": evidence.id,
            "link_role": link.evidence_role,
            "source_type": evidence.source_type or evidence.type,
            "uploaded_by_caregiver_id": evidence.uploaded_by_caregiver_id,
            "uploaded_at": evidence.uploaded_at.isoformat() if evidence.uploaded_at else None,
            "time_provenance": evidence.time_provenance,
            "source_span": link.source_span,
            "content_fingerprint": fingerprint,
            "integrity_status": "available",
            "source_independence": independence_review,
            "source_independence_review_id": review.id if review is not None else None,
            "source_independence_review_policy": review.policy_version if review is not None else None,
        })
    duplicate_fingerprints = {fingerprint for fingerprint, ids in groups.items() if len(ids) > 1}
    duplicate_content_groups = [
        {"fingerprint": fingerprint, "evidence_ids": ids, "interpretation": "Same normalized source text; do not count these records as independent corroboration without source review."}
        for fingerprint, ids in groups.items() if len(ids) > 1
    ]
    near_duplicate_analysis = _near_duplicate_candidates(list(evidence_by_id.values()))
    reviewed_independent_fingerprints = {
        fingerprint for fingerprint, results in fingerprint_reviews.items()
        if "independent_source_linked" in results
        and "copied_or_same_origin" not in results
        and fingerprint not in duplicate_fingerprints
    }
    if len(reviewed_independent_fingerprints) >= 2:
        corroboration_status = "reviewed_multiple_distinct_sources_candidate"
    elif len(reviewed_independent_fingerprints) == 1:
        corroboration_status = "only_one_reviewed_source_candidate"
    else:
        corroboration_status = "not_established"
    return {
        "person_id": person_id,
        "fact_id": fact_id,
        "linked_evidence_count": len(records),
        "distinct_nonempty_content_count": len(groups),
        "duplicate_content_candidates": duplicate_content_groups,
        "near_duplicate_analysis": near_duplicate_analysis,
        "independent_corroboration_status": corroboration_status,
        "interpretation_limit": "Different text does not prove independent origin. Identical normalized text is a duplicate-content candidate, not proof of copying. Near-duplicate candidates are heuristic text comparisons, not proof of copying. Reviewed source-independence labels are human review claims, not proof that the underlying claim is true.",
        "evidence": records,
    }


@router.post("/{resource}", status_code=201)
def create_resource(resource: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    if resource not in WRITABLE:
        raise HTTPException(status_code=404, detail="Unknown or read-only care-continuity resource")
    person_id, caregiver_id = _scoped_identity(request, db)
    allowed = WRITABLE[resource]
    data = {key: value for key, value in payload.items() if key in allowed}
    if not data:
        raise HTTPException(status_code=422, detail="No supported care-continuity fields supplied")
    required = {
        "facts": ("subject_type", "fact_type", "fact_value"),
        "assertions": ("subject_type", "subject_id", "field", "asserted_value", "source_label"),
        "fact-evidence": ("fact_id", "evidence_id"),
        "relationships": ("source_type", "source_id", "relationship_type", "target_type", "target_id"),
        "delta-items": ("delta_run_id", "item_type"),
        "integrity-findings": ("finding_type", "description"),
        "integrity-resolutions": ("finding_id", "resolution_status"),
        "decisions": ("title", "decision_text"),
        "decision-outcomes": ("decision_id", "outcome_status"),
        "reconciliations": ("target_type", "target_id", "reconciliation_status"),
        "follow-up-links": ("source_type", "source_id", "target_type", "target_id"),
        "review-decisions": ("target_type", "target_id", "review_decision"),
        "provenance": ("target_type", "target_id", "source_type"),
        "timeline": ("source_type", "source_id", "entry_type", "title"),
        "change-assessments": ("change_id",),
        "caregiver-contexts": ("context_type", "context_payload"),
        "caregiver-perspectives": ("topic", "perspective_text"),
        "caregiver-observations": ("observation_category", "description"),
        "living-records": (),
        "record-provenance": ("source_type",),
        "record-provenance-links": ("provenance_id", "target_type", "target_id"),
        "record-change-sets": ("operation",),
        "record-change-items": ("change_set_id", "target_type", "target_id", "operation"),
        "corrections": ("target_type", "target_id", "corrected_value", "correction_reason"),
        "amendments": ("amendment_type", "reason"),
        "amendment-items": ("amendment_id", "target_type", "target_id"),
        "record-audit": ("actor_type", "action"),
        "uncertainties": ("question", "uncertainty_type"),
        "missing-information": ("topic", "description"),
        "conflicts": ("conflict_type", "description"),
        "action-outcomes": ("action_id", "action_type", "outcome_status"),
        "outcomes": ("action_id", "action_type", "outcome_status"),
        "record-identifiers": ("care_record_id", "identifier_type", "identifier_fingerprint"),
        "record-sections": ("care_record_id", "section_key", "section_payload"),
        "record-links": ("source_record_id", "target_record_id", "relationship"),
        "record-retractions": ("care_record_id", "target_type", "target_id", "reason"),
        "record-restorations": ("care_record_id", "retraction_id", "reason"),
    }[resource]
    missing = [key for key in required if data.get(key) is None or data.get(key) == ""]
    if missing:
        raise HTTPException(status_code=422, detail={"missing_fields": missing})
    if resource == "assertions":
        assertion_kind = data.get("assertion_kind", "current_state")
        if assertion_kind not in {"current_state", "time_bound_observation"}:
            raise HTTPException(status_code=422, detail="Unsupported assertion kind")
        data["assertion_kind"] = assertion_kind
        # A newly submitted assertion is always a report. Confirmation requires
        # a separate reviewed transition and cannot be set by the caller.
        data["assertion_status"] = "reported"
        review_days = data.get("review_after_days")
        if review_days is not None:
            try:
                review_days = int(review_days)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="review_after_days must be a positive integer") from exc
            if review_days < 1 or review_days > 3650:
                raise HTTPException(status_code=422, detail="review_after_days must be between 1 and 3650")
            data["review_after_days"] = review_days
        if assertion_kind == "current_state" and review_days is None:
            raise HTTPException(status_code=422, detail="Current-state assertions require a caregiver-selected review window")
        if assertion_kind == "time_bound_observation" and not data.get("observed_at"):
            raise HTTPException(status_code=422, detail="Time-bound observations require the actual event time")
        source_event_id = data.get("source_event_id")
        if source_event_id:
            event_table = _table(db, "care_events")
            if not db.execute(select(event_table.c.id).where(
                event_table.c.id == source_event_id,
                event_table.c.person_id == person_id,
            )).first():
                raise HTTPException(status_code=404, detail="Source event was not found in this care record")

    if resource == "fact-evidence":
        fact_evidence = _table(db, "care_fact_evidence")
        duplicate = db.execute(
            select(fact_evidence.c.fact_id).where(
                fact_evidence.c.fact_id == data["fact_id"],
                fact_evidence.c.evidence_id == data["evidence_id"],
                fact_evidence.c.evidence_role == data.get("evidence_role", "supports"),
                fact_evidence.c.person_id == person_id,
            ).limit(1)
        ).first()
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail="This evidence is already linked to the claim; duplicate links cannot count as corroboration.",
            )
    _validate_links(db, person_id, resource, data)
    table = _table(db, RESOURCES[resource])
    data["person_id"] = person_id
    # Migration-created continuity tables use UUID-shaped string primary keys
    # without database-side generators; issue IDs server-side for every insert.
    if "id" in table.c and "id" not in data:
        data["id"] = str(uuid.uuid4())
    if "created_by_caregiver_id" in table.c:
        data["created_by_caregiver_id"] = caregiver_id
    # The legacy caregiver_id points to caregiver_profiles, not session caregivers.
    # New session attribution uses a separate server-bound column.
    if "caregiver_id" in table.c and resource not in ("caregiver-contexts", "caregiver-perspectives", "caregiver-observations"):
        data["caregiver_id"] = caregiver_id
    if resource in ("caregiver-contexts", "caregiver-perspectives", "caregiver-observations") and "reported_by_caregiver_id" in table.c:
        data["reported_by_caregiver_id"] = caregiver_id
    if resource == "caregiver-contexts" and "effective_start" in table.c and not data.get("effective_start"):
        data["effective_start"] = data.get("effective_from") or datetime.now(timezone.utc)
    if resource == "caregiver-perspectives":
        if "claim_type" in table.c:
            data["claim_type"] = ClaimType.INTERPRETATION.name
        if "claim_content" in table.c:
            data["claim_content"] = data.get("perspective_text")
        if "information_visibility" in table.c:
            data["information_visibility"] = InformationVisibility.PRIVATE.name
        if "is_promoted_to_shared" in table.c:
            data["is_promoted_to_shared"] = False
    if resource == "caregiver-observations":
        if "observation_content" in table.c:
            data["observation_content"] = data.get("description")
        if "observation_type" in table.c:
            # Missing observation basis is reported/second-hand, not silently
            # upgraded to direct observation. Explicit bases are preserved.
            raw_observation_type = str(data.get("observation_type") or "second_hand").strip().casefold()
            allowed_observation_types = {item.value.casefold(): item.name for item in ObservationType}
            allowed_observation_types.update({item.name.casefold(): item.name for item in ObservationType})
            normalized_observation_type = allowed_observation_types.get(raw_observation_type)
            if normalized_observation_type is None:
                raise HTTPException(status_code=422, detail="Unsupported observation type; choose direct, indirect, second_hand, or document_review")
            data["observation_type"] = normalized_observation_type
        if "information_visibility" in table.c:
            data["information_visibility"] = InformationVisibility.PRIVATE.name
        if "is_promoted_to_shared" in table.c:
            data["is_promoted_to_shared"] = False
        if data.get("observed_at"):
            try:
                observed_at = data["observed_at"]
                if isinstance(observed_at, str):
                    observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                if not isinstance(observed_at, datetime):
                    raise ValueError("observed_at must be an ISO-8601 datetime")
                data["observed_at"] = observed_at
                if "event_time" in table.c:
                    data["event_time"] = observed_at
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="observed_at must be a valid ISO-8601 datetime") from exc
    if resource == "record-provenance" and "author_caregiver_id" in table.c:
        data["author_caregiver_id"] = caregiver_id
    if resource == "record-provenance" and "method" in table.c and not data.get("method"):
        data["method"] = "manual"
    if resource == "record-change-sets" and "author_caregiver_id" in table.c:
        data["author_caregiver_id"] = caregiver_id
    if resource == "corrections" and "requested_by_caregiver_id" in table.c:
        data["requested_by_caregiver_id"] = caregiver_id
    if resource == "amendments" and "author_caregiver_id" in table.c:
        data["author_caregiver_id"] = caregiver_id
    if resource == "record-audit":
        data["actor_type"] = "caregiver"
        data["actor_id"] = caregiver_id
        if "occurred_at" in table.c:
            data["occurred_at"] = datetime.now(timezone.utc)
    # recorded_at is server receipt time, never a client-controlled timestamp.
    if resource in ("caregiver-contexts", "caregiver-perspectives", "caregiver-observations", "record-provenance") and "recorded_at" in table.c:
        data["recorded_at"] = datetime.now(timezone.utc)
    if resource == "record-change-sets" and not data.get("status"):
        data["status"] = "committed"
    if resource == "record-identifiers":
        fingerprint = str(data.get("identifier_fingerprint") or "")
        if not re.fullmatch(r"(?:[0-9a-fA-F]{64}|[0-9a-fA-F]{128})", fingerprint):
            raise HTTPException(status_code=422, detail="identifier_fingerprint must be a SHA-256 or SHA-512 hex digest")
        if not data.get("status"):
            data["status"] = "active"
    if resource == "record-links" and data.get("source_record_id") == data.get("target_record_id"):
        raise HTTPException(status_code=422, detail="A Living Care Record cannot link to itself")
    if resource == "record-sections":
        try:
            existing = db.execute(
                select(table).where(
                    table.c.person_id == person_id,
                    table.c.care_record_id == data["care_record_id"],
                    table.c.section_key == data["section_key"],
                )
            ).mappings().first()
            now = datetime.now(timezone.utc)
            if existing:
                db.execute(update(table).where(
                    table.c.id == existing["id"], table.c.person_id == person_id
                ).values(
                    section_payload=data["section_payload"],
                    version_number=int(existing.get("version_number") or 1) + 1,
                    updated_at=now,
                    updated_by_caregiver_id=caregiver_id,
                ))
                updated = db.execute(select(table).where(table.c.id == existing["id"], table.c.person_id == person_id)).mappings().one()
                db.commit()
                return _jsonable(dict(updated))
            data["version_number"] = 1
            data["updated_at"] = now
            data["updated_by_caregiver_id"] = caregiver_id
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Care record section could not be saved; refresh and retry") from exc
    if resource == "record-retractions":
        data["status"] = "retracted"
        data["retracted_by_caregiver_id"] = caregiver_id
        data["retracted_at"] = datetime.now(timezone.utc)
    if resource == "record-restorations":
        retractions = _table(db, "care_record_retractions")
        prior = db.execute(
            select(retractions.c.status).where(
                retractions.c.id == data["retraction_id"], retractions.c.person_id == person_id
            )
        ).scalar_one_or_none()
        if prior != "retracted":
            raise HTTPException(status_code=409, detail="Only an active retraction can be restored")
        data["restored_by_caregiver_id"] = caregiver_id
        data["restored_at"] = datetime.now(timezone.utc)
    if "recorded_by_caregiver_id" in table.c:
        data["recorded_by_caregiver_id"] = caregiver_id
    if "reviewed_by_caregiver_id" in table.c and resource == "review-decisions":
        data["reviewed_by_caregiver_id"] = caregiver_id
    if "assessed_by_caregiver_id" in table.c and resource == "change-assessments":
        data["assessed_by_caregiver_id"] = caregiver_id
    if "reviewed_by_caregiver_id" in table.c and resource == "delta-items" and data.get("reconciliation_status") != "pending_review":
        data["reviewed_by_caregiver_id"] = caregiver_id
        data["reviewed_at"] = datetime.now().astimezone()
    if "resolved_by_caregiver_id" in table.c and resource == "integrity-resolutions":
        data["resolved_by_caregiver_id"] = caregiver_id
    if "reviewed_by_caregiver_id" in table.c and resource == "reconciliations":
        data["reviewed_by_caregiver_id"] = caregiver_id
        data["reviewed_at"] = datetime.now().astimezone()
    if "evidence_ids" in data:
        if not isinstance(data["evidence_ids"], list):
            raise HTTPException(status_code=422, detail="evidence_ids must be a JSON array")
        if len(data["evidence_ids"]) != len(set(data["evidence_ids"])):
            raise HTTPException(status_code=422, detail="Duplicate evidence IDs are not allowed")
        if any(not isinstance(item, str) or not item.strip() for item in data["evidence_ids"]):
            raise HTTPException(status_code=422, detail="Every evidence ID must be a non-empty string")

    # Reflected legacy tables store JSONList fields as TEXT. Normalize structured
    # payloads and ISO-8601 timestamps to their actual database column types
    # before INSERT; never depend on a driver silently coercing browser strings.
    for key, value in list(data.items()):
        column = table.c.get(key)
        if column is None or value is None:
            continue
        if isinstance(column.type, SQLDateTime) and isinstance(value, str):
            try:
                data[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"{key} must be a valid ISO-8601 datetime") from exc
        elif isinstance(value, (list, dict)) and not isinstance(column.type, SQLJSON):
            if not isinstance(column.type, SQLText):
                raise HTTPException(status_code=422, detail=f"{key} must match the persisted column type")
            data[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    try:
        result = db.execute(insert(table).values(**data).returning(table))
        row = result.mappings().one()
        if resource == "record-change-items":
            _append_revision_for_change_item(db, person_id, caregiver_id, data)
        if resource == "living-records" and inspect(db.get_bind()).has_table("care_record_status_history"):
            status_history = _table(db, "care_record_status_history")
            db.execute(insert(status_history).values(
                id=str(uuid.uuid4()), person_id=person_id, care_record_id=row["id"],
                previous_status=None, new_status=row.get("record_status") or "active",
                reason="Living Care Record created", changed_by_caregiver_id=caregiver_id,
                changed_at=datetime.now(timezone.utc),
            ))
        if resource == "record-restorations":
            retractions = _table(db, "care_record_retractions")
            db.execute(update(retractions).where(
                retractions.c.id == data["retraction_id"], retractions.c.person_id == person_id
            ).values(status="restored"))
        db.commit()
        return _jsonable(dict(row))
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"Care-continuity record could not be saved ({type(exc).__name__}); check linked IDs and field types") from exc


SOURCE_RELATIONSHIP_TYPES = {
    "same_origin", "copied_from", "forwarded_from", "derived_from",
    "corroborates", "contradicts", "duplicates", "related_to",
}
SOURCE_INDEPENDENCE_STATUSES = {"independent", "shared_origin", "unknown", "not_assessed"}
LINEAGE_RELATIONSHIP_TYPES = {"copied_from", "forwarded_from", "derived_from"}
DEPENDENT_RELATIONSHIP_TYPES = LINEAGE_RELATIONSHIP_TYPES | {"same_origin"}


@router.post("/evidence/{evidence_id}/relationships", status_code=201)
def create_evidence_source_relationship(
    evidence_id: str, payload: dict[str, Any], request: Request, db: Session = Depends(get_db)
):
    """Append source-lineage/corroboration metadata; never infer independence from wording."""
    person_id, caregiver_id = _scoped_identity(request, db)
    source = db.query(Evidence).filter(
        Evidence.id == evidence_id, Evidence.person_id == person_id
    ).first()
    related_id = payload.get("related_evidence_id")
    related = db.query(Evidence).filter(
        Evidence.id == related_id, Evidence.person_id == person_id
    ).first() if isinstance(related_id, str) else None
    if source is None or related is None:
        raise HTTPException(status_code=404, detail="Evidence item not found in this care record")
    if source.id == related.id:
        raise HTTPException(status_code=422, detail="Evidence cannot be related to itself")
    relationship_type = payload.get("relationship_type")
    independence = payload.get("source_independence_status")
    rationale = payload.get("rationale")
    origin_group_key = payload.get("origin_group_key")
    if not isinstance(relationship_type, str) or relationship_type not in SOURCE_RELATIONSHIP_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported source relationship type")
    if not isinstance(independence, str) or independence not in SOURCE_INDEPENDENCE_STATUSES:
        raise HTTPException(status_code=422, detail="An explicit source-independence state is required")
    if not isinstance(rationale, str) or len(rationale.strip()) < 3:
        raise HTTPException(status_code=422, detail="A rationale of at least 3 characters is required")
    if origin_group_key is not None and (
        not isinstance(origin_group_key, str) or not origin_group_key.strip() or len(origin_group_key) > 128
    ):
        raise HTTPException(status_code=422, detail="origin_group_key must be a non-empty string up to 128 characters")
    if relationship_type in DEPENDENT_RELATIONSHIP_TYPES:
        if independence not in {"shared_origin", "unknown", "not_assessed"}:
            raise HTTPException(
                status_code=422,
                detail="Copied, forwarded, derived, and same-origin relationships cannot be labelled independent",
            )
        # Edges are dependent evidence -> origin evidence. Refuse a cycle in this lineage graph.
        from app.models.care_continuity import EvidenceSourceRelationship
        pending = [related.id]
        visited = set()
        while pending:
            current = pending.pop()
            if current == source.id:
                raise HTTPException(status_code=409, detail="This relationship would create a source-lineage cycle")
            if current in visited:
                continue
            visited.add(current)
            origins = db.query(EvidenceSourceRelationship.related_evidence_id).filter(
                EvidenceSourceRelationship.person_id == person_id,
                EvidenceSourceRelationship.source_evidence_id == current,
                EvidenceSourceRelationship.relationship_type.in_(LINEAGE_RELATIONSHIP_TYPES),
            ).all()
            pending.extend(row[0] for row in origins if row[0] not in visited)
    from app.models.care_continuity import EvidenceSourceRelationship
    row = EvidenceSourceRelationship(
        person_id=person_id,
        source_evidence_id=source.id,
        related_evidence_id=related.id,
        relationship_type=relationship_type,
        source_independence_status=independence,
        origin_group_key=origin_group_key.strip() if isinstance(origin_group_key, str) else None,
        rationale=rationale.strip(),
        reviewed_by_caregiver_id=caregiver_id,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This source relationship already exists") from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Evidence source relationship could not be persisted")
        raise HTTPException(status_code=503, detail="Evidence source relationship could not be persisted") from exc
    return {
        "id": row.id, "person_id": row.person_id,
        "source_evidence_id": row.source_evidence_id, "related_evidence_id": row.related_evidence_id,
        "relationship_type": row.relationship_type,
        "source_independence_status": row.source_independence_status,
        "origin_group_key": row.origin_group_key, "rationale": row.rationale,
        "reviewed_by_caregiver_id": row.reviewed_by_caregiver_id, "created_at": row.created_at,
    }


@router.get("/evidence/{evidence_id}/relationships")
def list_evidence_source_relationships(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    """Return only relationships for evidence visible within the authorized care record."""
    person_id, _ = _scoped_identity(request, db)
    evidence = db.query(Evidence.id).filter(
        Evidence.id == evidence_id, Evidence.person_id == person_id
    ).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence item not found")
    from app.models.care_continuity import EvidenceSourceRelationship
    rows = db.query(EvidenceSourceRelationship).filter(
        EvidenceSourceRelationship.person_id == person_id,
        (EvidenceSourceRelationship.source_evidence_id == evidence_id)
        | (EvidenceSourceRelationship.related_evidence_id == evidence_id),
    ).order_by(EvidenceSourceRelationship.created_at.asc(), EvidenceSourceRelationship.id.asc()).all()
    return [{
        "id": row.id, "person_id": row.person_id,
        "source_evidence_id": row.source_evidence_id, "related_evidence_id": row.related_evidence_id,
        "relationship_type": row.relationship_type,
        "source_independence_status": row.source_independence_status,
        "origin_group_key": row.origin_group_key, "rationale": row.rationale,
        "reviewed_by_caregiver_id": row.reviewed_by_caregiver_id, "created_at": row.created_at,
    } for row in rows]


OBSERVATION_KINDS = {
    "direct_observation", "caregiver_report", "professional_observation",
    "persons_own_report", "documented_record", "instrument_measurement",
    "photo_video", "audio_transcript", "imported_information",
    "historical_recollection", "derived_observation", "inference", "hypothesis",
}
OBSERVATION_EPISTEMIC_STATUSES = {
    "reported", "observed", "inferred", "hypothesis", "unknown",
    "missing", "disputed", "unverified", "superseded",
}
DIRECT_OBSERVATION_KINDS = {"direct_observation", "professional_observation", "instrument_measurement"}


def _observation_datetime(value: Any, field_name: str):
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{field_name} must be an ISO-8601 timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field_name} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise HTTPException(status_code=422, detail=f"{field_name} must include a timezone offset")
    return parsed


@router.post("/evidence/{evidence_id}/observations", status_code=201)
def create_evidence_observation(
    evidence_id: str, payload: dict[str, Any], request: Request, db: Session = Depends(get_db)
):
    """Persist a typed observation only when its quoted span exists in scoped original evidence."""
    person_id, caregiver_id = _scoped_identity(request, db)
    evidence = db.query(Evidence).filter(Evidence.id == evidence_id, Evidence.person_id == person_id).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    kind = payload.get("observation_kind")
    status = payload.get("epistemic_status")
    if not isinstance(kind, str) or kind not in OBSERVATION_KINDS:
        raise HTTPException(status_code=422, detail="Unsupported observation kind")
    if not isinstance(status, str) or status not in OBSERVATION_EPISTEMIC_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported epistemic status")
    subject = payload.get("subject_label")
    attribute = payload.get("attribute")
    passage = payload.get("source_passage")
    extraction_method = payload.get("extraction_method")
    extraction_version = payload.get("extraction_version")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 160:
        raise HTTPException(status_code=422, detail="subject_label is required and limited to 160 characters")
    if not isinstance(attribute, str) or not attribute.strip() or len(attribute) > 160:
        raise HTTPException(status_code=422, detail="attribute is required and limited to 160 characters")
    if not isinstance(passage, str) or not passage:
        raise HTTPException(status_code=422, detail="An exact non-empty source_passage is required")
    if not isinstance(evidence.source_text, str) or passage not in evidence.source_text:
        raise HTTPException(status_code=422, detail="source_passage must be an exact substring of the original evidence text")
    start, end = payload.get("source_span_start"), payload.get("source_span_end")
    occurrences = []
    cursor = 0
    while True:
        found = evidence.source_text.find(passage, cursor)
        if found < 0:
            break
        occurrences.append(found)
        cursor = found + 1
    if start is None and end is None:
        if len(occurrences) != 1:
            raise HTTPException(status_code=422, detail="Provide exact source offsets when the passage occurs more than once")
        start, end = occurrences[0], occurrences[0] + len(passage)
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
        raise HTTPException(status_code=422, detail="source span offsets must be integers")
    if start < 0 or end <= start or end > len(evidence.source_text) or evidence.source_text[start:end] != passage:
        raise HTTPException(status_code=422, detail="source span offsets must select the exact quoted passage")
    for field_name, maximum in (
        ("normalized_value", 8000), ("units", 80), ("context", 8000),
        ("observer_label", 2000), ("observation_method", 120),
        ("hedge_marker", 120), ("ambiguity_note", 4000),
        ("extraction_method", 80), ("extraction_version", 80), ("extraction_run_id", 128),
    ):
        field_value = payload.get(field_name)
        if field_value is not None and (not isinstance(field_value, str) or len(field_value) > maximum):
            raise HTTPException(status_code=422, detail=f"{field_name} must be a string up to {maximum} characters")
    if "negated" in payload and payload["negated"] is not None and not isinstance(payload["negated"], bool):
        raise HTTPException(status_code=422, detail="negated must be a boolean or null")
    value = payload.get("value_text")
    if value is not None and (not isinstance(value, str) or len(value) > 8000):
        raise HTTPException(status_code=422, detail="value_text must be a string up to 8000 characters")
    if status in {"unknown", "missing"}:
        if value not in (None, ""):
            raise HTTPException(status_code=422, detail="Unknown or missing observations cannot assert a value")
        value = None
    elif not isinstance(value, str):
        raise HTTPException(status_code=422, detail="A value is required unless the observation is explicitly unknown or missing")
    observer_label = payload.get("observer_label")
    observer_id = payload.get("observer_caregiver_id")
    method = payload.get("observation_method")
    if status == "observed":
        if kind not in DIRECT_OBSERVATION_KINDS or not isinstance(observer_label, str) or not observer_label.strip() or not isinstance(method, str) or not method.strip():
            raise HTTPException(status_code=422, detail="Observed status requires a direct-observation kind, named observer, and method")
    if observer_id is not None:
        observer = db.query(Caregiver.id).filter(Caregiver.id == observer_id, Caregiver.person_id == person_id).first()
        if observer is None:
            raise HTTPException(status_code=422, detail="Observer caregiver must belong to the authorized care record")
    # Prevent accidental duplicate extraction while preserving distinct source spans and epistemic states.
    from app.models.care_continuity import EvidenceObservation
    supersedes_id = payload.get("supersedes_observation_id")
    superseded_observation = None
    if supersedes_id is not None:
        if not isinstance(supersedes_id, str) or not supersedes_id.strip():
            raise HTTPException(status_code=422, detail="supersedes_observation_id must be a valid observation ID")
        superseded_observation = db.query(EvidenceObservation).filter(
            EvidenceObservation.id == supersedes_id,
            EvidenceObservation.person_id == person_id,
            EvidenceObservation.evidence_id == evidence_id,
        ).first()
        if superseded_observation is None:
            raise HTTPException(status_code=422, detail="The superseded observation must belong to this evidence and care record")
        if superseded_observation.attribute != attribute.strip():
            raise HTTPException(status_code=422, detail="A correction must retain the same observation attribute")
    correction_rationale = payload.get("correction_rationale")
    if superseded_observation is not None and (not isinstance(correction_rationale, str) or len(correction_rationale.strip()) < 3):
        raise HTTPException(status_code=422, detail="A correction rationale of at least 3 characters is required when superseding an observation")
    if correction_rationale is not None and (not isinstance(correction_rationale, str) or len(correction_rationale) > 4000):
        raise HTTPException(status_code=422, detail="correction_rationale must be a string up to 4000 characters")
    duplicate = db.query(EvidenceObservation.id).filter(
        EvidenceObservation.person_id == person_id,
        EvidenceObservation.evidence_id == evidence_id,
        EvidenceObservation.source_span_start == start,
        EvidenceObservation.source_span_end == end,
        EvidenceObservation.attribute == attribute.strip(),
        EvidenceObservation.observation_kind == kind,
        EvidenceObservation.epistemic_status == status,
        EvidenceObservation.value_text == value,
    ).first()
    if duplicate and superseded_observation is None:
        raise HTTPException(status_code=409, detail="An identical observation already exists; use supersedes_observation_id for a correction")
    row = EvidenceObservation(
        person_id=person_id, evidence_id=evidence_id, supersedes_observation_id=superseded_observation.id if superseded_observation else None,
        correction_rationale=correction_rationale.strip() if isinstance(correction_rationale, str) else None,
        observation_kind=kind, epistemic_status=status,
        subject_label=subject.strip(), attribute=attribute.strip(), value_text=value,
        normalized_value=payload.get("normalized_value"), units=payload.get("units"), context=payload.get("context"),
        occurred_at=_observation_datetime(payload.get("occurred_at"), "occurred_at"),
        observed_at=_observation_datetime(payload.get("observed_at"), "observed_at"),
        reported_at=_observation_datetime(payload.get("reported_at"), "reported_at"),
        observer_label=observer_label.strip() if isinstance(observer_label, str) else None,
        observer_caregiver_id=observer_id, observation_method=method.strip() if isinstance(method, str) else None,
        source_passage=passage, source_span_start=start, source_span_end=end,
        negated=payload.get("negated") if isinstance(payload.get("negated"), bool) else None,
        hedge_marker=payload.get("hedge_marker") if isinstance(payload.get("hedge_marker"), str) else None,
        ambiguity_note=payload.get("ambiguity_note") if isinstance(payload.get("ambiguity_note"), str) else None,
        extraction_method=extraction_method.strip() if isinstance(extraction_method, str) and extraction_method.strip() else "human_entered",
        extraction_version=extraction_version.strip() if isinstance(extraction_version, str) and extraction_version.strip() else "manual-v1",
        extraction_run_id=payload.get("extraction_run_id") if isinstance(payload.get("extraction_run_id"), str) else None,
        created_by_caregiver_id=caregiver_id,
    )
    try:
        db.add(row)
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This source-span observation already exists") from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Evidence observation could not be persisted")
        raise HTTPException(status_code=503, detail="Evidence observation could not be persisted") from exc
    return _evidence_observation_response(row)


def _evidence_observation_response(row):
    return {
        "id": row.id, "person_id": row.person_id, "evidence_id": row.evidence_id,
        "supersedes_observation_id": row.supersedes_observation_id,
        "correction_rationale": row.correction_rationale,
        "observation_kind": row.observation_kind, "epistemic_status": row.epistemic_status,
        "subject_label": row.subject_label, "attribute": row.attribute, "value_text": row.value_text,
        "normalized_value": row.normalized_value, "units": row.units, "context": row.context,
        "occurred_at": row.occurred_at, "observed_at": row.observed_at, "reported_at": row.reported_at,
        "observer_label": row.observer_label, "observer_caregiver_id": row.observer_caregiver_id,
        "observation_method": row.observation_method, "source_passage": row.source_passage,
        "source_span_start": row.source_span_start, "source_span_end": row.source_span_end,
        "negated": row.negated, "hedge_marker": row.hedge_marker, "ambiguity_note": row.ambiguity_note,
        "extraction_method": row.extraction_method, "extraction_version": row.extraction_version,
        "extraction_run_id": row.extraction_run_id, "created_by_caregiver_id": row.created_by_caregiver_id,
        "recorded_at": row.recorded_at,
    }


@router.get("/evidence/{evidence_id}/observations")
def list_evidence_observations(evidence_id: str, request: Request, db: Session = Depends(get_db)):
    person_id, _ = _scoped_identity(request, db)
    evidence = db.query(Evidence.id).filter(Evidence.id == evidence_id, Evidence.person_id == person_id).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    from app.models.care_continuity import EvidenceObservation
    rows = db.query(EvidenceObservation).filter(
        EvidenceObservation.person_id == person_id,
        EvidenceObservation.evidence_id == evidence_id,
    ).order_by(EvidenceObservation.source_span_start.asc(), EvidenceObservation.recorded_at.asc()).all()
    return [_evidence_observation_response(row) for row in rows]
