from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Set
from dataclasses import dataclass, field
from enum import Enum
import uuid
import json


class ProvenanceEventType(str, Enum):
    EXTRACTION = "extraction"
    PARSING = "parsing"
    RESOLUTION = "resolution"
    INFERENCE = "inference"
    CORRECTION = "correction"
    SUPERSESSION = "supersession"
    INVALIDATION = "invalidation"
    CONFLICT_DETECTION = "conflict_detection"
    CONFLICT_RESOLUTION = "conflict_resolution"
    RECOMPUTATION = "recomputation"


class TemporalFieldType(str, Enum):
    EVENT_TIME = "event_time"
    OBSERVATION_TIME = "observation_time"
    DOCUMENTATION_TIME = "documentation_time"
    AUTHORING_TIME = "authoring_time"
    SOURCE_TIME = "source_time"
    INGESTION_TIME = "ingestion_time"
    PROCESSING_TIME = "processing_time"
    EFFECTIVE_TIME = "effective_time"
    VALID_FROM = "valid_from"
    VALID_TO = "valid_to"


@dataclass
class ProvenanceRecord:
    """A single provenance record for a temporal fact"""
    provenance_id: str
    entity_type: str  # temporal_fact, temporal_relation, etc.
    entity_id: str
    field_name: str  # Which temporal field
    event_type: ProvenanceEventType
    previous_value: Optional[str] = None
    new_value: Optional[str] = None
    previous_uncertainty: Optional[str] = None
    new_uncertainty: Optional[str] = None
    confidence: float = 1.0
    reasoning: str = ""
    performed_by: str = "system"  # system, caregiver_id, clinician_id
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_evidence_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "provenance_id": self.provenance_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "field_name": self.field_name,
            "event_type": self.event_type.value,
            "previous_value": self.previous_value,
            "new_value": self.new_value,
            "previous_uncertainty": self.previous_uncertainty,
            "new_uncertainty": self.new_uncertainty,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "performed_by": self.performed_by,
            "timestamp": self.timestamp.isoformat(),
            "source_evidence_ids": self.source_evidence_ids,
            "metadata": self.metadata,
        }


@dataclass
class TemporalProvenanceChain:
    """Complete provenance chain for a temporal entity"""
    entity_id: str
    entity_type: str
    records: List[ProvenanceRecord] = field(default_factory=list)
    current_state: Dict[str, Any] = field(default_factory=dict)
    
    def add_record(self, record: ProvenanceRecord) -> None:
        self.records.append(record)
        # Update current state
        if record.field_name not in self.current_state:
            self.current_state[record.field_name] = {}
        self.current_state[record.field_name] = {
            "value": record.new_value,
            "uncertainty": record.new_uncertainty,
            "confidence": record.confidence,
            "last_updated": record.timestamp.isoformat(),
            "updated_by": record.performed_by,
        }
    
    def get_history(self, field_name: Optional[str] = None) -> List[ProvenanceRecord]:
        if field_name:
            return [r for r in self.records if r.field_name == field_name]
        return list(self.records)
    
    def get_original_value(self, field_name: str) -> Optional[str]:
        for record in self.records:
            if record.field_name == field_name and record.previous_value is None:
                return record.new_value
        return None
    
    def to_audit_trail(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.records]


class TemporalProvenanceStore:
    """Store and query temporal provenance"""
    
    def __init__(self):
        self.chains: Dict[str, TemporalProvenanceChain] = {}
    
    def get_chain(self, entity_id: str, entity_type: str) -> TemporalProvenanceChain:
        key = f"{entity_type}:{entity_id}"
        if key not in self.chains:
            self.chains[key] = TemporalProvenanceChain(entity_id=entity_id, entity_type=entity_type)
        return self.chains[key]
    
    def record_event(
        self,
        entity_type: str,
        entity_id: str,
        field_name: str,
        event_type: ProvenanceEventType,
        new_value: Optional[str],
        new_uncertainty: Optional[str] = None,
        confidence: float = 1.0,
        reasoning: str = "",
        performed_by: str = "system",
        source_evidence_ids: Optional[List[str]] = None,
        previous_value: Optional[str] = None,
        previous_uncertainty: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProvenanceRecord:
        chain = self.get_chain(entity_id, entity_type)
        
        record = ProvenanceRecord(
            provenance_id=f"prov_{uuid.uuid4().hex[:12]}",
            entity_type=entity_type,
            entity_id=entity_id,
            field_name=field_name,
            event_type=event_type,
            previous_value=previous_value,
            new_value=new_value,
            previous_uncertainty=previous_uncertainty,
            new_uncertainty=new_uncertainty,
            confidence=confidence,
            reasoning=reasoning,
            performed_by=performed_by,
            source_evidence_ids=source_evidence_ids or [],
            metadata=metadata or {},
        )
        
        chain.add_record(record)
        return record
    
    def get_full_provenance(self, entity_type: str, entity_id: str) -> Optional[TemporalProvenanceChain]:
        key = f"{entity_type}:{entity_id}"
        return self.chains.get(key)
    
    def query_provenance(
        self,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        field_name: Optional[str] = None,
        event_type: Optional[ProvenanceEventType] = None,
        performed_by: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[ProvenanceRecord]:
        results = []
        
        for chain in self.chains.values():
            if entity_type and chain.entity_type != entity_type:
                continue
            if entity_id and chain.entity_id != entity_id:
                continue
            
            for record in chain.records:
                if field_name and record.field_name != field_name:
                    continue
                if event_type and record.event_type != event_type:
                    continue
                if performed_by and record.performed_by != performed_by:
                    continue
                if since and record.timestamp < since:
                    continue
                if until and record.timestamp > until:
                    continue
                
                results.append(record)
        
        # Sort by timestamp
        results.sort(key=lambda r: r.timestamp)
        return results
    
    def get_field_history(self, entity_type: str, entity_id: str, field_name: str) -> List[ProvenanceRecord]:
        chain = self.get_chain(entity_id, entity_type)
        return chain.get_history(field_name)
    
    def export_audit_trail(self, entity_type: str, entity_id: str) -> List[Dict[str, Any]]:
        chain = self.get_chain(entity_id, entity_type)
        return chain.to_audit_trail()


def create_provenance_for_extraction(
    store: TemporalProvenanceStore,
    temporal_fact_id: str,
    field_name: TemporalFieldType,
    extracted_value: str,
    uncertainty: str,
    confidence: float,
    source_evidence_id: str,
    extraction_method: str,
    performed_by: str = "system",
) -> ProvenanceRecord:
    """Record provenance for initial extraction"""
    return store.record_event(
        entity_type="temporal_fact",
        entity_id=temporal_fact_id,
        field_name=field_name.value,
        event_type=ProvenanceEventType.EXTRACTION,
        new_value=extracted_value,
        new_uncertainty=uncertainty,
        confidence=confidence,
        reasoning=f"Extracted via {extraction_method}",
        performed_by=performed_by,
        source_evidence_ids=[source_evidence_id],
        metadata={"extraction_method": extraction_method},
    )


def create_provenance_for_resolution(
    store: TemporalProvenanceStore,
    temporal_fact_id: str,
    field_name: TemporalFieldType,
    resolved_value: str,
    resolved_uncertainty: str,
    confidence: float,
    reference_evidence_ids: List[str],
    resolution_method: str,
    performed_by: str = "system",
) -> ProvenanceRecord:
    """Record provenance for relative time resolution"""
    chain = store.get_chain(temporal_fact_id, "temporal_fact")
    previous = chain.current_state.get(field_name.value, {})
    
    return store.record_event(
        entity_type="temporal_fact",
        entity_id=temporal_fact_id,
        field_name=field_name.value,
        event_type=ProvenanceEventType.RESOLUTION,
        previous_value=previous.get("value"),
        new_value=resolved_value,
        previous_uncertainty=previous.get("uncertainty"),
        new_uncertainty=resolved_uncertainty,
        confidence=confidence,
        reasoning=f"Resolved relative time via {resolution_method}",
        performed_by=performed_by,
        source_evidence_ids=reference_evidence_ids,
        metadata={"resolution_method": resolution_method},
    )


def create_provenance_for_correction(
    store: TemporalProvenanceStore,
    temporal_fact_id: str,
    field_name: TemporalFieldType,
    corrected_value: str,
    corrected_uncertainty: str,
    confidence: float,
    reason: str,
    corrected_by: str,
    source_evidence_ids: List[str],
) -> ProvenanceRecord:
    """Record provenance for a correction"""
    chain = store.get_chain(temporal_fact_id, "temporal_fact")
    previous = chain.current_state.get(field_name.value, {})
    
    return store.record_event(
        entity_type="temporal_fact",
        entity_id=temporal_fact_id,
        field_name=field_name.value,
        event_type=ProvenanceEventType.CORRECTION,
        previous_value=previous.get("value"),
        new_value=corrected_value,
        previous_uncertainty=previous.get("uncertainty"),
        new_uncertainty=corrected_uncertainty,
        confidence=confidence,
        reasoning=f"Corrected: {reason}",
        performed_by=corrected_by,
        source_evidence_ids=source_evidence_ids,
        metadata={"correction_reason": reason},
    )


def create_provenance_for_supersession(
    store: TemporalProvenanceStore,
    old_fact_id: str,
    new_fact_id: str,
    superseded_by: str,
    reason: str,
) -> Tuple[ProvenanceRecord, ProvenanceRecord]:
    """Record provenance for fact supersession"""
    chain_old = store.get_chain(old_fact_id, "temporal_fact")
    chain_new = store.get_chain(new_fact_id, "temporal_fact")
    
    # Mark old as superseded
    old_record = store.record_event(
        entity_type="temporal_fact",
        entity_id=old_fact_id,
        field_name="status",
        event_type=ProvenanceEventType.SUPERSESSION,
        previous_value="active",
        new_value="superseded",
        confidence=1.0,
        reasoning=f"Superseded by {new_fact_id}: {reason}",
        performed_by=superseded_by,
        metadata={"superseded_by_fact_id": new_fact_id, "reason": reason},
    )
    
    # Mark new as active
    new_record = store.record_event(
        entity_type="temporal_fact",
        entity_id=new_fact_id,
        field_name="status",
        event_type=ProvenanceEventType.SUPERSESSION,
        previous_value=None,
        new_value="active",
        confidence=1.0,
        reasoning=f"Supersedes {old_fact_id}: {reason}",
        performed_by=superseded_by,
        metadata={"supersedes_fact_id": old_fact_id, "reason": reason},
    )
    
    return old_record, new_record


def create_provenance_for_invalidation(
    store: TemporalProvenanceStore,
    temporal_fact_id: str,
    reason: str,
    invalidated_by: str,
    source_evidence_ids: List[str],
) -> ProvenanceRecord:
    """Record provenance for fact invalidation"""
    return store.record_event(
        entity_type="temporal_fact",
        entity_id=temporal_fact_id,
        field_name="status",
        event_type=ProvenanceEventType.INVALIDATION,
        previous_value="active",
        new_value="invalidated",
        confidence=1.0,
        reasoning=f"Invalidated: {reason}",
        performed_by=invalidated_by,
        source_evidence_ids=source_evidence_ids,
        metadata={"invalidation_reason": reason},
    )


def create_provenance_for_inference(
    store: TemporalProvenanceStore,
    conclusion_id: str,
    inferred_value: str,
    confidence: float,
    derivation_chain: List[str],
    evidence_ids: List[str],
    inference_method: str,
    performed_by: str = "system",
) -> ProvenanceRecord:
    """Record provenance for an inferred temporal conclusion"""
    return store.record_event(
        entity_type="temporal_conclusion",
        entity_id=conclusion_id,
        field_name="inferred_value",
        event_type=ProvenanceEventType.INFERENCE,
        new_value=inferred_value,
        new_uncertainty="inferred",
        confidence=confidence,
        reasoning=f"Inferred via {inference_method} from {len(derivation_chain)} premises",
        performed_by=performed_by,
        source_evidence_ids=evidence_ids,
        metadata={
            "inference_method": inference_method,
            "derivation_chain": derivation_chain,
        },
    )


def trace_temporal_conclusion(
    store: TemporalProvenanceStore,
    conclusion_id: str,
    max_depth: int = 10
) -> Dict[str, Any]:
    """
    Trace a temporal conclusion back to its original evidence.
    Returns the complete derivation tree.
    """
    # This would traverse the derivation chain
    # For now, return the direct provenance
    records = store.query_provenance(
        entity_type="temporal_conclusion",
        entity_id=conclusion_id,
    )
    
    trace = {
        "conclusion_id": conclusion_id,
        "direct_provenance": [r.to_dict() for r in records],
        "evidence_ids": [],
        "derivation_depth": 0,
    }
    
    for record in records:
        trace["evidence_ids"].extend(record.source_evidence_ids)
        if "derivation_chain" in record.metadata:
            trace["derivation_depth"] = len(record.metadata["derivation_chain"])
    
    # Deduplicate
    trace["evidence_ids"] = list(set(trace["evidence_ids"]))
    
    return trace


def verify_provenance_completeness(
    store: TemporalProvenanceStore,
    entity_type: str,
    entity_id: str,
    required_fields: List[str]
) -> Dict[str, Any]:
    """
    Verify that all required temporal fields have provenance.
    """
    chain = store.get_chain(entity_id, entity_type)
    
    field_provenance = {}
    for field in required_fields:
        history = chain.get_history(field)
        field_provenance[field] = {
            "has_provenance": len(history) > 0,
            "record_count": len(history),
            "last_event": history[-1].event_type.value if history else None,
            "last_updated": history[-1].timestamp.isoformat() if history else None,
        }
    
    missing = [f for f, p in field_provenance.items() if not p["has_provenance"]]
    
    return {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "complete": len(missing) == 0,
        "missing_fields": missing,
        "field_provenance": field_provenance,
    }


# Global store instance
_global_provenance_store: Optional[TemporalProvenanceStore] = None


def get_provenance_store() -> TemporalProvenanceStore:
    global _global_provenance_store
    if _global_provenance_store is None:
        _global_provenance_store = TemporalProvenanceStore()
    return _global_provenance_store


# Export
__all__ = [
    "ProvenanceEventType",
    "TemporalFieldType",
    "ProvenanceRecord",
    "TemporalProvenanceChain",
    "TemporalProvenanceStore",
    "get_provenance_store",
    "create_provenance_for_extraction",
    "create_provenance_for_resolution",
    "create_provenance_for_correction",
    "create_provenance_for_supersession",
    "create_provenance_for_invalidation",
    "create_provenance_for_inference",
    "trace_temporal_conclusion",
    "verify_provenance_completeness",
]