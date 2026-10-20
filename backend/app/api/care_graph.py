# Care Graph API - RESTful endpoints for the Care Graph Engine.

from fastapi import APIRouter, Depends, HTTPException, Path, Body, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from uuid import UUID

from app.core.database import get_db
from app.services.care_graph_service import CareGraphService
from app.services.care_graph_query import CareGraphQueryEngine
from app.services.care_graph_evolution import CareGraphEvolutionService
from app.services.care_graph_integration import CareGraphIntegrationService
from app.models.care_graph import CareGraphNode, CareGraphEdge
from app.core.graph_enums import CareGraphNodeType, CareGraphRelationshipType, CareGraphRelationshipStatus, CareGraphTemporalStatus
from app.models.identity import Person
from app.core.security import get_current_active_user, AuthenticatedPrincipal
from app.core.cache_decorators import cached_response


from app.schemas.care_graph import (
    CareGraphNodeCreate,
    CareGraphNodeUpdate,
    CareGraphNodeResponse,
    CareGraphEdgeCreate,
    CareGraphEdgeUpdate,
    CareGraphEdgeResponse,
)

router = APIRouter(prefix="/graph", tags=["care_graph"])

def verify_person_access(person_id: str, current_user: AuthenticatedPrincipal):
    """Verify that the current user has access to the specified person's graph."""
    if not current_user.has_access_to_person(person_id):
        raise HTTPException(status_code=403, detail="Not authorized to access this person's graph")

def get_care_graph_service(db: Session = Depends(get_db)) -> CareGraphService:
    return CareGraphService(db)

def get_care_graph_query_engine(db: Session = Depends(get_db)) -> CareGraphQueryEngine:
    return CareGraphQueryEngine(db)

def get_care_graph_evolution_service(db: Session = Depends(get_db)) -> CareGraphEvolutionService:
    return CareGraphEvolutionService(db)

def get_care_graph_integration_service(db: Session = Depends(get_db)) -> CareGraphIntegrationService:
    return CareGraphIntegrationService(db)

@cached_response(ttl=60, key_prefix="care_graph")
# ============================================================
# NODE ENDPOINTS
# ============================================================

@router.post("/{person_id}/nodes", response_model=CareGraphNodeResponse, status_code=status.HTTP_201_CREATED)
def create_node(
    person_id: str = Path(..., description="Person ID"),
    node_in: CareGraphNodeCreate = Body(...),
    service: CareGraphService = Depends(get_care_graph_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Create a new node in the person's care graph."""
    verify_person_access(person_id, current_user)
    # Ensure person_id matches
    if node_in.person_id != person_id:
        raise HTTPException(status_code=400, detail="Person ID in path must match person_id in body")
    node = service.create_node(
        person_id=person_id,
        node_type=node_in.node_type,
        canonical_entity_id=node_in.canonical_entity_id,
        canonical_entity_type=node_in.canonical_entity_type,
        label=node_in.label,
        created_by=node_in.created_by,
        created_by_system=node_in.created_by_system,
        provenance_source=node_in.provenance_source,
        provenance_details=node_in.provenance_details,
        valid_from=node_in.valid_from,
        valid_to=node_in.valid_to,
        temporal_status=node_in.temporal_status,
        entity_resolution_status=node_in.entity_resolution_status,
        entity_resolution_confidence=node_in.entity_resolution_confidence,
        unresolved_identity_reason=node_in.unresolved_identity_reason,
        quality_gate_passed=node_in.quality_gate_passed,
        quality_gate_details=node_in.quality_gate_details,
        processing_version=node_in.processing_version,
    )
    return node

@cached_response(ttl=3600, key_prefix="care_graph")
@router.get("/{person_id}/nodes/{node_id}", response_model=CareGraphNodeResponse)
def get_node(
    person_id: str = Path(..., description="Person ID"),
    node_id: str = Path(..., description="Node ID"),
    service: CareGraphService = Depends(get_care_graph_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Get a node by ID."""
    verify_person_access(person_id, current_user)
    node = service.get_node(node_id=node_id, person_id=person_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node

@router.put("/{person_id}/nodes/{node_id}", response_model=CareGraphNodeResponse)
def update_node(
    person_id: str = Path(..., description="Person ID"),
    node_id: str = Path(..., description="Node ID"),
    node_in: CareGraphNodeUpdate = Body(...),
    service: CareGraphService = Depends(get_care_graph_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Update a node."""
    verify_person_access(person_id, current_user)
    node = service.update_node(
        node_id=node_id,
        person_id=person_id,
        updates=node_in.model_dump(exclude_unset=True),
        changed_by=current_user.account.id,
        changed_by_system=False,
    )
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node

@router.delete("/{person_id}/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_node(
    person_id: str = Path(..., description="Person ID"),
    node_id: str = Path(..., description="Node ID"),
    service: CareGraphService = Depends(get_care_graph_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Delete a node (soft delete)."""
    verify_person_access(person_id, current_user)
    success = service.delete_node(
        node_id=node_id,
        person_id=person_id,
        deleted_by=current_user.account.id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Node not found")
    return None

# ============================================================
# EDGE ENDPOINTS
# ============================================================

@router.post("/{person_id}/edges", response_model=CareGraphEdgeResponse, status_code=status.HTTP_201_CREATED)
def create_edge(
    person_id: str = Path(..., description="Person ID"),
    edge_in: CareGraphEdgeCreate = Body(...),
    service: CareGraphService = Depends(get_care_graph_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Create a new edge in the person's care graph."""
    verify_person_access(person_id, current_user)
    # Ensure person_id matches
    if edge_in.person_id != person_id:
        raise HTTPException(status_code=400, detail="Person ID in path must match person_id in body")
    edge = service.create_edge(
        person_id=person_id,
        subject_node_id=edge_in.subject_node_id,
        object_node_id=edge_in.object_node_id,
        relationship_type=edge_in.relationship_type,
        relationship_subtype=edge_in.relationship_subtype,
        directionality=edge_in.directionality,
        valid_from=edge_in.valid_from,
        valid_to=edge_in.valid_to,
        temporal_status=edge_in.temporal_status,
        temporal_precision=edge_in.temporal_precision,
        status=edge_in.status,
        confidence_existence=edge_in.confidence.existence if edge_in.confidence else None,
        confidence_subject=edge_in.confidence.subject if edge_in.confidence else None,
        confidence_object=edge_in.confidence.object if edge_in.confidence else None,
        confidence_type=edge_in.confidence.type if edge_in.confidence else None,
        confidence_temporal=edge_in.confidence.temporal if edge_in.confidence else None,
        confidence_identity=edge_in.confidence.identity if edge_in.confidence else None,
        confidence_source_support=edge_in.confidence.source_support if edge_in.confidence else None,
        confidence_causal=edge_in.confidence.causal if edge_in.confidence else None,
        confidence_overall=edge_in.confidence.overall if edge_in.confidence else None,
        provenance_source=edge_in.provenance_source,
        inference_method=edge_in.inference_method,
        reasoning_version=edge_in.reasoning_version,
        supporting_evidence_ids=edge_in.supporting_evidence_ids,
        contradicting_evidence_ids=edge_in.contradicting_evidence_ids,
        source_relationship_ids=edge_in.source_relationship_ids,
        causal_basis=edge_in.causal_basis,
        alternative_explanations=edge_in.alternative_explanations,
        confounders=edge_in.confounders,
        strength=edge_in.strength,
        edge_metadata=edge_in.edge_metadata,
        created_by=edge_in.created_by,
        created_by_system=edge_in.created_by_system,
        created_by_type=edge_in.created_by_type,
    )
    return edge

@cached_response(ttl=3600, key_prefix="care_graph")
@router.get("/{person_id}/edges/{edge_id}", response_model=CareGraphEdgeResponse)
def get_edge(
    person_id: str = Path(..., description="Person ID"),
    edge_id: str = Path(..., description="Edge ID"),
    service: CareGraphService = Depends(get_care_graph_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Get an edge by ID."""
    verify_person_access(person_id, current_user)
    edge = service.get_edge(edge_id=edge_id, person_id=person_id)
    if not edge:
        raise HTTPException(status_code=404, detail="Edge not found")
    return edge

@router.put("/{person_id}/edges/{edge_id}", response_model=CareGraphEdgeResponse)
def update_edge(
    person_id: str = Path(..., description="Person ID"),
    edge_id: str = Path(..., description="Edge ID"),
    edge_in: CareGraphEdgeUpdate = Body(...),
    service: CareGraphService = Depends(get_care_graph_service)
):
    """Update an edge."""
    verify_person_access(person_id, current_user)
    updates = edge_in.model_dump(exclude_unset=True)
    # Handle confidence subfields if present
    if 'confidence' in updates and updates['confidence'] is not None:
        conf = updates['confidence']
        # Flatten confidence dimensions into service parameters
        if conf.existence is not None:
            updates['confidence_existence'] = conf.existence
        if conf.subject is not None:
            updates['confidence_subject'] = conf.subject
        if conf.object is not None:
            updates['confidence_object'] = conf.object
        if conf.type is not None:
            updates['confidence_type'] = conf.type
        if conf.temporal is not None:
            updates['confidence_temporal'] = conf.temporal
        if conf.identity is not None:
            updates['confidence_identity'] = conf.identity
        if conf.source_support is not None:
            updates['confidence_source_support'] = conf.source_support
        if conf.causal is not None:
            updates['confidence_causal'] = conf.causal
        if conf.overall is not None:
            updates['confidence_overall'] = conf.overall
        # Remove the confidence dict as service expects individual fields
        del updates['confidence']
    edge = service.update_edge(
        edge_id=edge_id,
        person_id=person_id,
        updates=updates,
        changed_by=current_user.account.id,
        changed_by_system=False,
    )
    if not edge:
        raise HTTPException(status_code=404, detail="Edge not found")
    return edge

@router.delete("/{person_id}/edges/{edge_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_edge(
    person_id: str = Path(..., description="Person ID"),
    edge_id: str = Path(..., description="Edge ID"),
    service: CareGraphService = Depends(get_care_graph_service),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Delete an edge (soft delete)."""
    verify_person_access(person_id, current_user)
    success = service.delete_edge(
        edge_id=edge_id,
        person_id=person_id,
        deleted_by=current_user.account.id
    )
    if not success:
        raise HTTPException(status_code=404, detail="Edge not found")
    return None

# ============================================================
# QUERY ENDPOINTS (using query engine)
# ============================================================

@cached_response(ttl=3600, key_prefix="care_graph")
@router.get("/{person_id}/snapshot")
def get_graph_snapshot(
    person_id: str = Path(..., description="Person ID"),
    at_time: Optional[datetime] = Query(None, description="Timestamp for snapshot (defaults to now)"),
    include_inactive: bool = Query(False, description="Include inactive/temporal status != CURRENT items"),
    query_engine: CareGraphQueryEngine = Depends(get_care_graph_query_engine),
    current_user: AuthenticatedPrincipal = Depends(get_current_active_user)
):
    """Get a snapshot of the person's graph at a point in time."""
    verify_person_access(person_id, current_user)
    if at_time is None:
        at_time = datetime.now(timezone.utc)
    snapshot = query_engine.get_graph_snapshot(
        person_id=person_id,
        at_time=at_time,
        include_inactive=include_inactive
    )
    return snapshot

# TODO: Additional query endpoints (neighbors, paths, histories, evidence, contradictions, degree) can be added here