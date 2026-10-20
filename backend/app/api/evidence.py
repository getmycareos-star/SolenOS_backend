from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
from app.services.evidence_service import EvidenceService
from app.schemas.evidence import (
    EvidenceCreate,
    EvidenceUpdate,
    EvidenceResponse,
    EvidenceQueryParams,
    EvidenceTraceResponse,
    EvidenceGraphResponse,
    EvidenceRelationshipCreate,
    EvidenceRelationshipResponse,
    EvidenceCorrectionCreate,
    EvidenceCorrectionResponse,
    EvidenceInvalidationCreate,
    EvidenceInvalidationResponse,
    EvidenceSupersessionCreate,
    EvidenceSupersessionResponse,
    EvidenceDeduplicationCreate,
    EvidenceDeduplicationResponse,
)
from app.core.cache_decorators import cached_response

router = APIRouter(prefix="/evidence", tags=["evidence"])


def get_evidence_service(db: Session = Depends(get_db)) -> EvidenceService:
    return EvidenceService(db)


@router.post("/", response_model=EvidenceResponse, status_code=status.HTTP_201_CREATED)
def create_evidence(
    evidence: EvidenceCreate,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Create new evidence."""
    return service.create_evidence(evidence)


@cached_response(ttl=3600, key_prefix="evidence")
@router.get("/{evidence_id}", response_model=EvidenceResponse)
def get_evidence(
    evidence_id: str,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Get evidence by ID."""
    db_evidence = service.get_evidence(evidence_id)
    if db_evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return db_evidence


@router.patch("/{evidence_id}", response_model=EvidenceResponse)
def update_evidence(
    evidence_id: str,
    evidence_update: EvidenceUpdate,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Update evidence."""
    # We need to pass the caregiver_id from the authenticated user, but for now we'll use a placeholder.
    # In a real implementation, we would get the caregiver_id from the auth context.
    updated_evidence = service.update_evidence(evidence_id, evidence_update, caregiver_id="temp_caregiver_id")
    if updated_evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return updated_evidence


@router.delete("/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_evidence(
    evidence_id: str,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Delete evidence (soft delete)."""
    success = service.delete_evidence(evidence_id)
    if not success:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return None


@cached_response(ttl=300, key_prefix="evidence_list")
@router.get("/", response_model=List[EvidenceResponse])
def query_evidence(
    service: EvidenceService = Depends(get_evidence_service),
    # We'll accept query parameters as per EvidenceQueryParams, but for simplicity we'll just take a few.
    # In a full implementation, we would map all the query parameters.
    person_id: Optional[str] = Query(None),
    subject_id: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Query evidence with filters."""
    params = EvidenceQueryParams(
        person_id=person_id,
        subject_id=subject_id,
        type=type,
        status=status,
        limit=limit,
        offset=offset,
    )
    results, total = service.query_evidence(params)
    return results


@router.post("/{evidence_id}/relationships", response_model=EvidenceRelationshipResponse)
def create_relationship(
    evidence_id: str,
    relationship: EvidenceRelationshipCreate,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Create a relationship for evidence."""
    # Ensure the source_evidence_id matches the evidence_id in the path
    if relationship.source_evidence_id != evidence_id:
        raise HTTPException(status_code=400, detail="Source evidence ID must match the evidence ID in the path")
    return service.create_relationship(relationship)


@cached_response(ttl=3600, key_prefix="evidence_relationships")
@router.get("/{evidence_id}/relationships", response_model=List[EvidenceRelationshipResponse])
def get_relationships(
    evidence_id: str,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Get relationships for evidence."""
    relationships = service.get_relationships(evidence_id)
    return relationships


@router.post("/{evidence_id}/corrections", response_model=EvidenceCorrectionResponse)
def create_correction(
    evidence_id: str,
    correction: EvidenceCorrectionCreate,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Create a correction for evidence."""
    if correction.evidence_id != evidence_id:
        raise HTTPException(status_code=400, detail="Evidence ID must match the evidence ID in the path")
    return service.create_correction(correction)


@router.post("/{evidence_id}/invalidations", response_model=EvidenceInvalidationResponse)
def create_invalidation(
    evidence_id: str,
    invalidation: EvidenceInvalidationCreate,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Create an invalidation for evidence."""
    if invalidation.evidence_id != evidence_id:
        raise HTTPException(status_code=400, detail="Evidence ID must match the evidence ID in the path")
    return service.create_invalidation(invalidation)


@router.post("/{evidence_id}/supersessions", response_model=EvidenceSupersessionResponse)
def create_supersession(
    evidence_id: str,
    supersession: EvidenceSupersessionCreate,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Create a supersession for evidence."""
    if supersession.superseded_evidence_id != evidence_id and supersession.superseding_evidence_id != evidence_id:
        raise HTTPException(status_code=400, detail="One of the evidence IDs must match the evidence ID in the path")
    return service.create_supersession(supersession)


@router.post("/{evidence_id}/deduplication", response_model=EvidenceDeduplicationResponse)
def propose_deduplication(
    evidence_id: str,
    dedup: EvidenceDeduplicationCreate,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Propose deduplication for evidence."""
    if dedup.evidence_id_a != evidence_id and dedup.evidence_id_b != evidence_id:
        raise HTTPException(status_code=400, detail="One of the evidence IDs must match the evidence ID in the path")
    return service.propose_deduplication(dedup)


@cached_response(ttl=3600, key_prefix="evidence_trace")
@router.get("/{evidence_id}/trace", response_model=EvidenceTraceResponse)
def get_trace(
    evidence_id: str,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Get traceability chain for evidence."""
    trace = service.get_trace(evidence_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return trace


@cached_response(ttl=3600, key_prefix="evidence_graph")
@router.get("/graph/{person_id}", response_model=EvidenceGraphResponse)
def get_evidence_graph(
    person_id: str,
    service: EvidenceService = Depends(get_evidence_service),
):
    """Get evidence graph for a person."""
    return service.get_evidence_graph(person_id)
