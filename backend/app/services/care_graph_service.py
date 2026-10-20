# Care Graph Core Service

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timezone
import json
import logging

from app.models.care_graph import (
    CareGraphNode,
    CareGraphEdge,
    CareGraphEdgeVersion,
    CareGraphNodeVersion,
    CareGraphChangeLog,
    CareGraphEdgeCorrection,
    CareGraphEdgeInvalidation,
    CareGraphEdgeSupersession,
    CareGraphContradiction,
    CareGraphGroup,
)
from app.core.graph_enums import (
    CareGraphNodeType,
    CareGraphRelationshipType,
    CareGraphRelationshipSubtype,
    CareGraphRelationshipStatus,
    CareGraphDirectionality,
    CareGraphTemporalStatus,
    CareGraphChangeType,
    CareGraphCorrectionType,
    CareGraphProvenanceSource,
    CareGraphInferenceMethod,
)
from app.models.identity import Person

logger = logging.getLogger(__name__)


class CareGraphService:
    def __init__(self, db: Session):
        self.db = db


    # ============================================================
    # NODE OPERATIONS
    # ============================================================
    
    def create_node(
        self,
        person_id: str,
        node_type: CareGraphNodeType,
        canonical_entity_id: str,
        canonical_entity_type: str,
        label: Optional[str] = None,
        created_by: Optional[str] = None,
        created_by_system: bool = False,
        provenance_source: CareGraphProvenanceSource = CareGraphProvenanceSource.MANUAL_ENTRY,
        provenance_details: Optional[Dict[str, Any]] = None,
        valid_from: Optional[datetime] = None,
        valid_to: Optional[datetime] = None,
        temporal_status: CareGraphTemporalStatus = CareGraphTemporalStatus.CURRENT,
        entity_resolution_status: str = 'resolved',
        entity_resolution_confidence: Optional[float] = None,
        unresolved_identity_reason: Optional[str] = None,
        quality_gate_passed: bool = False,
        quality_gate_details: Optional[Dict[str, Any]] = None,
        processing_version: Optional[str] = None,
    ) -> CareGraphNode:
        '''Create a new care graph node.'''
        
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f'Person {person_id} not found')
        
        # Create node
        node = CareGraphNode(
            person_id=person_id,
            node_type=node_type,
            canonical_entity_id=canonical_entity_id,
            canonical_entity_type=canonical_entity_type,
            label=label,
            created_by=created_by,
            created_by_system=created_by_system,
            provenance_source=provenance_source,
            provenance_details=provenance_details or {},
            valid_from=valid_from,
            valid_to=valid_to,
            temporal_status=temporal_status,
            entity_resolution_status=entity_resolution_status,
            entity_resolution_confidence=entity_resolution_confidence,
            unresolved_identity_reason=unresolved_identity_reason,
            quality_gate_passed=quality_gate_passed,
            quality_gate_details=quality_gate_details or {},
            processing_version=processing_version,
        )
        
        self.db.add(node)
        self.db.flush()  # Get the ID without committing
        
        # Create initial version
        self._create_node_version(node, CareGraphChangeType.CREATED, created_by, created_by_system)
        
        # Log change
        self._log_change(
            node_id=node.id,
            change_type=CareGraphChangeType.CREATED,
            new_version=node.version,
            triggered_by=created_by,
            triggered_by_system=created_by_system,
            reason='Node created',
        )
        
        self.db.commit()
        self.db.refresh(node)
        return node
    
    def get_node(self, node_id: str, person_id: str) -> Optional[CareGraphNode]:
        '''Get a node by ID, enforcing person boundary.'''
        return self.db.query(CareGraphNode).filter(
            and_(
                CareGraphNode.id == node_id,
                CareGraphNode.person_id == person_id
            )
        ).first()
    
    def get_nodes_by_person(
        self,
        person_id: str,
        node_type: Optional[CareGraphNodeType] = None,
        canonical_entity_type: Optional[str] = None,
        temporal_status: Optional[CareGraphTemporalStatus] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[CareGraphNode]:
        '''Get nodes for a person with optional filtering.'''
        query = self.db.query(CareGraphNode).filter(CareGraphNode.person_id == person_id)
        
        if node_type:
            query = query.filter(CareGraphNode.node_type == node_type)
        if canonical_entity_type:
            query = query.filter(CareGraphNode.canonical_entity_type == canonical_entity_type)
        if temporal_status:
            query = query.filter(CareGraphNode.temporal_status == temporal_status)
            
        return query.offset(offset).limit(limit).all()
    
    def update_node(
        self,
        node_id: str,
        person_id: str,
        updates: Dict[str, Any],
        changed_by: Optional[str] = None,
        changed_by_system: bool = False,
        change_reason: Optional[str] = None,
        triggering_evidence_ids: Optional[List[str]] = None,
    ) -> Optional[CareGraphNode]:
        '''Update a node and create a new version.'''
        
        node = self.get_node(node_id, person_id)
        if not node:
            return None
        
        old_version = node.version
        
        # Apply updates
        for key, value in updates.items():
            if hasattr(node, key):
                setattr(node, key, value)
        
        node.updated_at = datetime.now(timezone.utc)
        node.version += 1
        
        self.db.flush()
        
        # Create new version
        self._create_node_version(
            node, 
            CareGraphChangeType.CORRECTED if change_reason else CareGraphChangeType.UPDATED,
            changed_by,
            changed_by_system,
            change_reason,
            triggering_evidence_ids
        )
        
        # Log change
        self._log_change(
            node_id=node.id,
            change_type=CareGraphChangeType.CORRECTED if change_reason else CareGraphChangeType.UPDATED,
            old_version=old_version,
            new_version=node.version,
            triggered_by=changed_by,
            triggered_by_system=changed_by_system,
            reason=change_reason or 'Node updated',
            triggered_by_evidence_ids=triggering_evidence_ids,
        )
        
        self.db.commit()
        self.db.refresh(node)
        return node
    
    def delete_node(self, node_id: str, person_id: str, deleted_by: Optional[str] = None) -> bool:
        '''Soft delete a node by setting temporal status to historical.'''
        node = self.get_node(node_id, person_id)
        if not node:
            return False
        
        old_version = node.version
        node.temporal_status = CareGraphTemporalStatus.HISTORICAL
        node.valid_to = datetime.now(timezone.utc)
        node.version += 1
        node.updated_at = datetime.now(timezone.utc)
        
        self.db.flush()
        
        # Create version for deletion
        self._create_node_version(
            node,
            CareGraphChangeType.EXPIRED,
            deleted_by,
            False,  # System-initiated
            'Node deleted',
        )
        
        # Log change
        self._log_change(
            node_id=node.id,
            change_type=CareGraphChangeType.EXPIRED,
            old_version=old_version,
            new_version=node.version,
            triggered_by=deleted_by,
            triggered_by_system=False,
            reason='Node deleted',
        )
        
        self.db.commit()
        return True


    # ============================================================
    # EDGE OPERATIONS
    # ============================================================
    
    def create_edge(
        self,
        person_id: str,
        subject_node_id: str,
        object_node_id: str,
        relationship_type: CareGraphRelationshipType,
        relationship_subtype: Optional[CareGraphRelationshipSubtype] = None,
        directionality: CareGraphDirectionality = CareGraphDirectionality.DIRECTED,
        valid_from: Optional[datetime] = None,
        valid_to: Optional[datetime] = None,
        temporal_status: CareGraphTemporalStatus = CareGraphTemporalStatus.CURRENT,
        temporal_precision: Optional[str] = None,
        status: CareGraphRelationshipStatus = CareGraphRelationshipStatus.CANDIDATE,
        confidence_existence: Optional[float] = None,
        confidence_subject: Optional[float] = None,
        confidence_object: Optional[float] = None,
        confidence_type: Optional[float] = None,
        confidence_temporal: Optional[float] = None,
        confidence_identity: Optional[float] = None,
        confidence_source_support: Optional[float] = None,
        confidence_causal: Optional[float] = None,
        confidence_overall: Optional[float] = None,
        provenance_source: CareGraphProvenanceSource = CareGraphProvenanceSource.MANUAL_ENTRY,
        inference_method: Optional[CareGraphInferenceMethod] = None,
        reasoning_version: Optional[str] = None,
        supporting_evidence_ids: Optional[List[str]] = None,
        contradicting_evidence_ids: Optional[List[str]] = None,
        source_relationship_ids: Optional[List[str]] = None,
        causal_basis: Optional[str] = None,
        alternative_explanations: Optional[List[str]] = None,
        confounders: Optional[List[str]] = None,
        strength: Optional[float] = None,
        edge_metadata: Optional[Dict[str, Any]] = None,
        created_by: Optional[str] = None,
        created_by_system: bool = False,
        created_by_type: Optional[str] = None,
    ) -> CareGraphEdge:
        '''Create a new care graph edge.'''
        
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f'Person {person_id} not found')
        
        # Validate nodes belong to the same person
        subject_node = self.get_node(subject_node_id, person_id)
        object_node = self.get_node(object_node_id, person_id)
        if not subject_node or not object_node:
            raise ValueError('Subject and object nodes must exist and belong to the same person')
        
        # Check for duplicate edge (Section 34)
        existing_edge = self.db.query(CareGraphEdge).filter(
            and_(
                CareGraphEdge.person_id == person_id,
                CareGraphEdge.subject_node_id == subject_node_id,
                CareGraphEdge.object_node_id == object_node_id,
                CareGraphEdge.relationship_type == relationship_type,
                CareGraphEdge.status != CareGraphRelationshipStatus.INVALIDATED
            )
        ).first()
        
        if existing_edge:
            # Return existing edge instead of creating duplicate
            logger.info(f'Returning existing edge {existing_edge.id} instead of creating duplicate')
            return existing_edge
        
        # Create edge
        edge = CareGraphEdge(
            person_id=person_id,
            subject_node_id=subject_node_id,
            subject_node_type=subject_node.node_type,
            object_node_id=object_node_id,
            object_node_type=object_node.node_type,
            relationship_type=relationship_type,
            relationship_subtype=relationship_subtype,
            directionality=directionality,
            valid_from=valid_from,
            valid_to=valid_to,
            temporal_status=temporal_status,
            temporal_precision=temporal_precision,
            status=status,
            confidence_existence=confidence_existence,
            confidence_subject=confidence_subject,
            confidence_object=confidence_object,
            confidence_type=confidence_type,
            confidence_temporal=confidence_temporal,
            confidence_identity=confidence_identity,
            confidence_source_support=confidence_source_support,
            confidence_causal=confidence_causal,
            confidence_overall=confidence_overall,
            provenance_source=provenance_source,
            inference_method=inference_method,
            reasoning_version=reasoning_version,
            supporting_evidence_ids=supporting_evidence_ids or [],
            contradicting_evidence_ids=contradicting_evidence_ids or [],
            source_relationship_ids=source_relationship_ids or [],
            causal_basis=causal_basis,
            alternative_explanations=alternative_explanations or [],
            confounders=confounders or [],
            strength=strength,
            edge_metadata=edge_metadata or {},
            created_by=created_by,
            created_by_system=created_by_system,
            created_by_type=created_by_type,
        )
        
        self.db.add(edge)
        self.db.flush()  # Get the ID without committing
        
        # Create initial version
        self._create_edge_version(edge, CareGraphChangeType.CREATED, created_by, created_by_system)
        
        # Log change
        self._log_change(
            edge_id=edge.id,
            change_type=CareGraphChangeType.CREATED,
            new_version=edge.version,
            triggered_by=created_by,
            triggered_by_system=created_by_system,
            reason='Edge created',
        )
        
        self.db.commit()
        self.db.refresh(edge)
        return edge
    
    def get_edge(self, edge_id: str, person_id: str) -> Optional[CareGraphEdge]:
        '''Get an edge by ID, enforcing person boundary.'''
        return self.db.query(CareGraphEdge).filter(
            and_(
                CareGraphEdge.id == edge_id,
                CareGraphEdge.person_id == person_id
            )
        ).first()
    
    def get_edges_by_person(
        self,
        person_id: str,
        relationship_type: Optional[CareGraphRelationshipType] = None,
        status: Optional[CareGraphRelationshipStatus] = None,
        temporal_status: Optional[CareGraphTemporalStatus] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[CareGraphEdge]:
        '''Get edges for a person with optional filtering.'''
        query = self.db.query(CareGraphEdge).filter(CareGraphEdge.person_id == person_id)
        
        if relationship_type:
            query = query.filter(CareGraphEdge.relationship_type == relationship_type)
        if status:
            query = query.filter(CareGraphEdge.status == status)
        if temporal_status:
            query = query.filter(CareGraphEdge.temporal_status == temporal_status)
            
        return query.offset(offset).limit(limit).all()
    
    def get_edges_between_nodes(
        self,
        person_id: str,
        subject_node_id: str,
        object_node_id: str,
        relationship_type: Optional[CareGraphRelationshipType] = None
    ) -> List[CareGraphEdge]:
        '''Get edges between two specific nodes.'''
        query = self.db.query(CareGraphEdge).filter(
            and_(
                CareGraphEdge.person_id == person_id,
                CareGraphEdge.subject_node_id == subject_node_id,
                CareGraphEdge.object_node_id == object_node_id
            )
        )
        
        if relationship_type:
            query = query.filter(CareGraphEdge.relationship_type == relationship_type)
            
        return query.all()
    
    def update_edge(
        self,
        edge_id: str,
        person_id: str,
        updates: Dict[str, Any],
        changed_by: Optional[str] = None,
        changed_by_system: bool = False,
        change_reason: Optional[str] = None,
        triggering_evidence_ids: Optional[List[str]] = None,
    ) -> Optional[CareGraphEdge]:
        '''Update an edge and create a new version.'''
        
        edge = self.get_edge(edge_id, person_id)
        if not edge:
            return None
        
        old_version = edge.version
        
        # Apply updates
        for key, value in updates.items():
            if hasattr(edge, key):
                setattr(edge, key, value)
        
        edge.updated_at = datetime.now(timezone.utc)
        edge.version += 1
        
        self.db.flush()
        
        # Create new version
        self._create_edge_version(
            edge, 
            CareGraphChangeType.CORRECTED if change_reason else CareGraphChangeType.UPDATED,
            changed_by,
            changed_by_system,
            change_reason,
            triggering_evidence_ids
        )
        
        # Log change
        self._log_change(
            edge_id=edge.id,
            change_type=CareGraphChangeType.CORRECTED if change_reason else CareGraphChangeType.UPDATED,
            old_version=old_version,
            new_version=edge.version,
            triggered_by=changed_by,
            triggered_by_system=changed_by_system,
            reason=change_reason or 'Edge updated',
            triggered_by_evidence_ids=triggering_evidence_ids,
        )
        
        self.db.commit()
        self.db.refresh(edge)
        return edge
    
    def delete_edge(self, edge_id: str, person_id: str, deleted_by: Optional[str] = None) -> bool:
        '''Soft delete an edge by setting status to invalidated.'''
        edge = self.get_edge(edge_id, person_id)
        if not edge:
            return False
        
        old_version = edge.version
        edge.status = CareGraphRelationshipStatus.INVALIDATED
        edge.valid_to = datetime.now(timezone.utc)
        edge.version += 1
        edge.updated_at = datetime.now(timezone.utc)
        
        self.db.flush()
        
        # Create version for deletion
        self._create_edge_version(
            edge,
            CareGraphChangeType.INVALIDATED,
            deleted_by,
            False,  # System-initiated
            'Edge deleted',
        )
        
        # Log change
        self._log_change(
            edge_id=edge.id,
            change_type=CareGraphChangeType.INVALIDATED,
            old_version=old_version,
            new_version=edge.version,
            triggered_by=deleted_by,
            triggered_by_system=False,
            reason='Edge deleted',
        )
        
        self.db.commit()
        return True


    # ============================================================
    # VERSIONING METHODS
    # ============================================================
    
    def _create_node_version(
        self,
        node: CareGraphNode,
        change_type: CareGraphChangeType,
        changed_by: Optional[str] = None,
        changed_by_system: bool = False,
        change_reason: Optional[str] = None,
        triggering_evidence_ids: Optional[List[str]] = None,
    ) -> CareGraphNodeVersion:
        '''Create an immutable version of a node.'''
        
        version = CareGraphNodeVersion(
            node_id=node.id,
            version=node.version,
            person_id=node.person_id,
            node_type=node.node_type,
            canonical_entity_id=node.canonical_entity_id,
            canonical_entity_type=node.canonical_entity_type,
            label=node.label,
            entity_resolution_status=node.entity_resolution_status,
            entity_resolution_confidence=node.entity_resolution_confidence,
            unresolved_identity_reason=node.unresolved_identity_reason,
            valid_from=node.valid_from,
            valid_to=node.valid_to,
            temporal_status=node.temporal_status,
            provenance_source=node.provenance_source,
            provenance_details=node.provenance_details,
            change_type=change_type,
            change_reason=change_reason,
            changed_by=changed_by,
            changed_by_system=changed_by_system,
            triggering_evidence_ids=triggering_evidence_ids or [],
        )
        
        self.db.add(version)
        return version
    
    def _create_edge_version(
        self,
        edge: CareGraphEdge,
        change_type: CareGraphChangeType,
        changed_by: Optional[str] = None,
        changed_by_system: bool = False,
        change_reason: Optional[str] = None,
        triggering_evidence_ids: Optional[List[str]] = None,
    ) -> CareGraphEdgeVersion:
        '''Create an immutable version of an edge.'''
        
        version = CareGraphEdgeVersion(
            edge_id=edge.id,
            version=edge.version,
            person_id=edge.person_id,
            subject_node_id=edge.subject_node_id,
            subject_node_type=edge.subject_node_type,
            object_node_id=edge.object_node_id,
            object_node_type=edge.object_node_type,
            relationship_type=edge.relationship_type,
            relationship_subtype=edge.relationship_subtype,
            directionality=edge.directionality,
            valid_from=edge.valid_from,
            valid_to=edge.valid_to,
            temporal_status=edge.temporal_status,
            temporal_precision=edge.temporal_precision,
            status=edge.status,
            confidence_existence=edge.confidence_existence,
            confidence_subject=edge.confidence_subject,
            confidence_object=edge.confidence_object,
            confidence_type=edge.confidence_type,
            confidence_temporal=edge.confidence_temporal,
            confidence_identity=edge.confidence_identity,
            confidence_source_support=edge.confidence_source_support,
            confidence_causal=edge.confidence_causal,
            confidence_overall=edge.confidence_overall,
            provenance_source=edge.provenance_source,
            inference_method=edge.inference_method,
            reasoning_version=edge.reasoning_version,
            supporting_evidence_ids=edge.supporting_evidence_ids,
            contradicting_evidence_ids=edge.contradicting_evidence_ids,
            source_relationship_ids=edge.source_relationship_ids,
            causal_basis=edge.causal_basis,
            alternative_explanations=edge.alternative_explanations,
            confounders=edge.confounders,
            strength=edge.strength,
            edge_metadata=edge.edge_metadata,
            change_type=change_type,
            change_reason=change_reason,
            changed_by=changed_by,
            changed_by_system=changed_by_system,
            triggering_evidence_ids=triggering_evidence_ids or [],
        )
        
        self.db.add(version)
        return version
    
    def get_node_versions(self, node_id: str, person_id: str) -> List[CareGraphNodeVersion]:
        '''Get all versions of a node.'''
        node = self.get_node(node_id, person_id)
        if not node:
            return []
            
        return self.db.query(CareGraphNodeVersion).filter(
            CareGraphNodeVersion.node_id == node_id
        ).order_by(CareGraphNodeVersion.version.asc()).all()
    
    def get_edge_versions(self, edge_id: str, person_id: str) -> List[CareGraphEdgeVersion]:
        '''Get all versions of an edge.'''
        edge = self.get_edge(edge_id, person_id)
        if not edge:
            return []
            
        return self.db.query(CareGraphEdgeVersion).filter(
            CareGraphEdgeVersion.edge_id == edge_id
        ).order_by(CareGraphEdgeVersion.version.asc()).all()


    # ============================================================
    # CHANGE LOG METHODS
    # ============================================================
    
    def _log_change(
        self,
        node_id: Optional[str] = None,
        edge_id: Optional[str] = None,
        change_type: CareGraphChangeType = CareGraphChangeType.CREATED,
        old_version: Optional[int] = None,
        new_version: Optional[int] = None,
        triggered_by: Optional[str] = None,
        triggered_by_system: bool = False,
        reasoning_version: Optional[str] = None,
        reason: Optional[str] = None,
        triggered_by_evidence_ids: Optional[List[str]] = None,
        affected_edges: Optional[List[str]] = None,
        affected_nodes: Optional[List[str]] = None,
        affected_situations: Optional[List[str]] = None,
        affected_states: Optional[List[str]] = None,
        affected_watches: Optional[List[str]] = None,
    ) -> CareGraphChangeLog:
        '''Log a change to the graph.'''
        
        change_log = CareGraphChangeLog(
            node_id=node_id,
            edge_id=edge_id,
            change_type=change_type,
            old_version=old_version,
            new_version=new_version,
            actor_id=triggered_by,
            actor_type='system' if triggered_by_system else 'user',
            reasoning_version=reasoning_version,
            reason=reason,
            triggering_evidence_ids=triggered_by_evidence_ids or [],
            affected_edges=affected_edges or [],
            affected_nodes=affected_nodes or [],
            affected_situations=affected_situations or [],
            affected_states=affected_states or [],
            affected_watches=affected_watches or [],
        )
        
        self.db.add(change_log)
        self.db.flush()
        return change_log
    
    def get_change_log(
        self,
        person_id: str,
        node_id: Optional[str] = None,
        edge_id: Optional[str] = None,
        change_type: Optional[CareGraphChangeType] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[CareGraphChangeLog]:
        '''Get change log entries for a person.'''
        query = self.db.query(CareGraphChangeLog).join(
            CareGraphNode, CareGraphChangeLog.node_id == CareGraphNode.id, isouter=True
        ).join(
            CareGraphEdge, CareGraphChangeLog.edge_id == CareGraphEdge.id, isouter=True
        ).filter(
            or_(
                CareGraphNode.person_id == person_id,
                CareGraphEdge.person_id == person_id
            )
        )
        
        if node_id:
            query = query.filter(CareGraphChangeLog.node_id == node_id)
        if edge_id:
            query = query.filter(CareGraphChangeLog.edge_id == edge_id)
        if change_type:
            query = query.filter(CareGraphChangeLog.change_type == change_type)
            
        return query.order_by(CareGraphChangeLog.created_at.desc()).offset(offset).limit(limit).all()


    # ============================================================
    # CORRECTION, INVALIDATION, SUPERSESSION METHODS
    # ============================================================
    
    def correct_edge(
        self,
        edge_id: str,
        person_id: str,
        field_name: str,
        old_value: Any,
        new_value: Any,
        corrected_by: Optional[str] = None,
        correction_reason: Optional[str] = None,
        correction_evidence_ids: Optional[List[str]] = None,
        is_source_correction: bool = False,
        source_correction_details: Optional[str] = None,
    ) -> Optional[CareGraphEdgeCorrection]:
        '''Record a correction to an edge.'''
        
        edge = self.get_edge(edge_id, person_id)
        if not edge:
            return None
        
        correction = CareGraphEdgeCorrection(
            edge_id=edge_id,
            correction_type=CareGraphCorrectionType.CORRECTION,
            field_name=field_name,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            corrected_by=corrected_by,
            correction_reason=correction_reason,
            correction_evidence_ids=correction_evidence_ids or [],
            is_source_correction=is_source_correction,
            source_correction_details=source_correction_details,
        )
        
        self.db.add(correction)
        
        # Log the correction
        self._log_change(
            edge_id=edge_id,
            change_type=CareGraphChangeType.CORRECTED,
            old_version=edge.version,
            new_version=edge.version + 1,
            triggered_by=corrected_by,
            triggered_by_system=False,
            reason=correction_reason or f'Corrected {field_name}',
            triggered_by_evidence_ids=correction_evidence_ids,
        )
        
        # Increment edge version for the correction
        edge.version += 1
        edge.updated_at = datetime.now(timezone.utc)
        
        self.db.commit()
        self.db.refresh(correction)
        return correction
    
    def invalidate_edge(
        self,
        edge_id: str,
        person_id: str,
        reason: str,
        invalidated_by: Optional[str] = None,
        reason_details: Optional[str] = None,
    ) -> Optional[CareGraphEdgeInvalidation]:
        '''Record an invalidation of an edge.'''
        
        edge = self.get_edge(edge_id, person_id)
        if not edge:
            return None
        
        invalidation = CareGraphEdgeInvalidation(
            edge_id=edge_id,
            reason=reason,
            reason_details=reason_details,
            invalidated_by=invalidated_by,
        )
        
        self.db.add(invalidation)
        
        # Log the invalidation
        self._log_change(
            edge_id=edge_id,
            change_type=CareGraphChangeType.INVALIDATED,
            old_version=edge.version,
            new_version=edge.version + 1,
            triggered_by=invalidated_by,
            triggered_by_system=False,
            reason=reason,
        )
        
        # Increment edge version and mark as invalidated
        edge.version += 1
        edge.status = CareGraphRelationshipStatus.INVALIDATED
        edge.valid_to = datetime.now(timezone.utc)
        edge.updated_at = datetime.now(timezone.utc)
        
        self.db.commit()
        self.db.refresh(invalidation)
        return invalidation
    
    def supersede_edge(
        self,
        superseded_edge_id: str,
        superseding_edge_id: str,
        person_id: str,
        reason: str,
        reason_details: Optional[str] = None,
        established_by: Optional[str] = None,
        established_by_system: bool = False,
        effective_from: Optional[datetime] = None,
        is_explicit: bool = True,
    ) -> Optional[CareGraphEdgeSupersession]:
        '''Record that one edge supersedes another.'''
        
        superseded_edge = self.get_edge(superseded_edge_id, person_id)
        superseding_edge = self.get_edge(superseding_edge_id, person_id)
        
        if not superseded_edge or not superseding_edge:
            return None
        
        supersession = CareGraphEdgeSupersession(
            superseded_edge_id=superseded_edge_id,
            superseding_edge_id=superseding_edge_id,
            reason=reason,
            reason_details=reason_details,
            established_by=established_by,
            established_by_system=established_by_system,
            effective_from=effective_from or datetime.now(timezone.utc),
            is_explicit=is_explicit,
        )
        
        self.db.add(supersession)
        
        # Log the supersession for both edges
        self._log_change(
            edge_id=superseded_edge_id,
            change_type=CareGraphChangeType.SUPERSEDED,
            old_version=superseded_edge.version,
            new_version=superseded_edge.version + 1,
            triggered_by=established_by,
            triggered_by_system=established_by_system,
            reason=reason,
        )
        
        self._log_change(
            edge_id=superseding_edge_id,
            change_type=CareGraphChangeType.CREATED,  # The superseding edge is considered new
            old_version=None,
            new_version=superseding_edge.version,
            triggered_by=established_by,
            triggered_by_system=established_by_system,
            reason=reason,
        )
        
        # Mark the superseded edge as superseded
        superseded_edge.version += 1
        superseded_edge.status = CareGraphRelationshipStatus.SUPERSEDED
        superseded_edge.valid_to = datetime.now(timezone.utc)
        superseded_edge.updated_at = datetime.now(timezone.utc)
        
        self.db.commit()
        self.db.refresh(supersession)
        return supersession


    # ============================================================
    # CONTRADICTION METHODS
    # ============================================================
    
    def create_contradiction(
        self,
        person_id: str,
        edge_a_id: str,
        edge_b_id: str,
        contradiction_type: str,
        description: str,
        edge_a_evidence_ids: Optional[List[str]] = None,
        edge_b_evidence_ids: Optional[List[str]] = None,
        detected_by: Optional[str] = None,
        detection_method: Optional[str] = None,
        detection_confidence: Optional[float] = None,
    ) -> Optional[CareGraphContradiction]:
        '''Create a contradiction between two edges.'''
        
        edge_a = self.get_edge(edge_a_id, person_id)
        edge_b = self.get_edge(edge_b_id, person_id)
        
        if not edge_a or not edge_b:
            return None
        
        contradiction = CareGraphContradiction(
            person_id=person_id,
            edge_a_id=edge_a_id,
            edge_b_id=edge_b_id,
            contradiction_type=contradiction_type,
            description=description,
            edge_a_evidence_ids=edge_a_evidence_ids or [],
            edge_b_evidence_ids=edge_b_evidence_ids or [],
            detected_by=detected_by,
            detection_method=detection_method,
            detection_confidence=detection_confidence,
        )
        
        self.db.add(contradiction)
        self.db.commit()
        self.db.refresh(contradiction)
        return contradiction
    
    # ============================================================
    # GROUPING METHODS
    # ============================================================
    
    def create_group(
        self,
        person_id: str,
        group_type: str,
        title: str,
        description: Optional[str] = None,
        temporal_start: Optional[datetime] = None,
        temporal_end: Optional[datetime] = None,
        member_node_ids: Optional[List[str]] = None,
        member_edge_ids: Optional[List[str]] = None,
        grouping_confidence: Optional[float] = None,
        grouping_reason: Optional[float] = None,
        created_by: Optional[str] = None,
        created_by_system: bool = False,
        detection_method: Optional[str] = None,
        detection_evidence_ids: Optional[List[str]] = None,
        representative_node_id: Optional[str] = None,
        representative_edge_id: Optional[str] = None,
    ) -> CareGraphGroup:
        '''Create a care graph group.'''
        
        # Enforce person boundary
        person = self.db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f'Person {person_id} not found')
        
        group = CareGraphGroup(
            person_id=person_id,
            group_type=group_type,
            title=title,
            description=description,
            temporal_start=temporal_start,
            temporal_end=temporal_end,
            member_node_ids=member_node_ids or [],
            member_edge_ids=member_edge_ids or [],
            member_count=len(member_node_ids or []),
            grouping_confidence=grouping_confidence,
            grouping_reason=grouping_reason,
            created_by=created_by,
            created_by_system=created_by_system,
            detection_method=detection_method,
            detection_evidence_ids=detection_evidence_ids or [],
            representative_node_id=representative_node_id,
            representative_edge_id=representative_edge_id,
        )
        
        self.db.add(group)
        self.db.commit()
        self.db.refresh(group)
        return group



