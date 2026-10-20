from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import copy


class KnowledgeStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    UNCERTAIN = "uncertain"
    CONFLICTED = "conflicted"
    SUPERSEDED = "superseded"


@dataclass
class TemporalKnowledge:
    """A piece of knowledge with temporal validity"""
    knowledge_id: str
    subject_type: str  # condition, medication, event, etc.
    subject_id: str
    claim: str  # What is claimed
    confidence: float
    status: KnowledgeStatus
    valid_from: datetime  # When this knowledge became valid
    valid_until: Optional[datetime] = None  # When it was superseded/invalidated
    evidence_ids: List[str] = field(default_factory=list)
    source: str = ""
    provenance: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.valid_from.tzinfo is None:
            self.valid_from = self.valid_from.replace(tzinfo=timezone.utc)
        if self.valid_until and self.valid_until.tzinfo is None:
            self.valid_until = self.valid_until.replace(tzinfo=timezone.utc)
    
    def is_valid_at(self, point_in_time: datetime) -> bool:
        """Check if this knowledge was valid at a given point in time"""
        point = point_in_time
        if point.tzinfo is None:
            point = point.replace(tzinfo=timezone.utc)
        if point < self.valid_from:
            return False
        if self.valid_until and point >= self.valid_until:
            return False
        return True


@dataclass
class HistoricalKnowledgeSnapshot:
    """What the system knew at a specific point in time"""
    as_of: datetime
    person_id: str
    known_claims: List[TemporalKnowledge]
    uncertain_claims: List[TemporalKnowledge]
    conflicting_claims: List[Dict[str, Any]]
    unknown_future: List[str]  # Things that were not yet known
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TemporalEvidence:
    """Evidence with temporal metadata"""
    evidence_id: str
    content: str
    evidence_type: str  # document, observation, lab, etc.
    documented_at: datetime  # When it was documented
    event_time: Optional[datetime] = None  # When the event occurred
    ingestion_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))  # When system received it
    author: Optional[str] = None
    source: str = ""
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        for field_name in ['documented_at', 'event_time', 'ingestion_time']:
            val = getattr(self, field_name)
            if val and val.tzinfo is None:
                setattr(self, field_name, val.replace(tzinfo=timezone.utc))


@dataclass
class KnowledgeDependency:
    """Dependency between knowledge pieces"""
    dependency_id: str
    depends_on: str  # knowledge_id
    dependent: str  # knowledge_id
    dependency_type: str  # "temporal", "causal", "logical"
    description: str


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_knowledge_timeline(
    evidence: List[TemporalEvidence],
    extraction_fn=None
) -> List[TemporalKnowledge]:
    """
    Build a timeline of knowledge from evidence.
    extraction_fn: function(evidence) -> List[dict] with keys: subject_type, subject_id, claim, confidence, etc.
    """
    if extraction_fn is None:
        extraction_fn = _default_extraction
    
    knowledge_items = []
    
    for ev in evidence:
        extracted = extraction_fn(ev)
        for item in extracted:
            knowledge = TemporalKnowledge(
                knowledge_id=f"k_{uuid.uuid4().hex[:8]}",
                subject_type=item.get("subject_type", "unknown"),
                subject_id=item.get("subject_id", "unknown"),
                claim=item.get("claim", ""),
                confidence=item.get("confidence", 0.5),
                status=KnowledgeStatus.KNOWN,
                valid_from=ev.ingestion_time,  # Knowledge enters system at ingestion
                evidence_ids=[ev.evidence_id],
                source=ev.source,
                provenance={
                    "evidence_id": ev.evidence_id,
                    "documented_at": ev.documented_at.isoformat(),
                    "event_time": ev.event_time.isoformat() if ev.event_time else None,
                    "author": ev.author,
                }
            )
            knowledge_items.append(knowledge)
    
    # Sort by valid_from
    knowledge_items.sort(key=lambda k: k.valid_from)
    
    # Handle supersession
    knowledge_items = _resolve_supersession(knowledge_items)
    
    return knowledge_items


def _default_extraction(evidence: TemporalEvidence) -> List[Dict[str, Any]]:
    """Default extraction - in practice would use NLP"""
    # This is a placeholder - real implementation would parse the content
    return [{
        "subject_type": "observation",
        "subject_id": f"obs_{evidence.evidence_id}",
        "claim": evidence.content[:200],
        "confidence": evidence.confidence,
    }]


def _resolve_supersession(
    knowledge: List[TemporalKnowledge]
) -> List[TemporalKnowledge]:
    """Mark superseded knowledge"""
    # Group by subject
    by_subject: Dict[str, List[TemporalKnowledge]] = defaultdict(list)
    for k in knowledge:
        key = f"{k.subject_type}:{k.subject_id}"
        by_subject[key].append(k)
    
    # For each subject, sort by time and mark supersession
    for key, items in by_subject.items():
        items.sort(key=lambda k: k.valid_from)
        for i in range(len(items) - 1):
            # If later knowledge contradicts earlier, mark earlier as superseded
            if _contradicts(items[i], items[i+1]):
                items[i].status = KnowledgeStatus.SUPERSEDED
                items[i].valid_until = items[i+1].valid_from
    
    return knowledge


def _contradicts(k1: TemporalKnowledge, k2: TemporalKnowledge) -> bool:
    """Check if two knowledge items contradict"""
    # Simple contradiction: opposite claims
    if "no " in k1.claim.lower() and "no " not in k2.claim.lower():
        # Check if they're about the same thing
        return k1.subject_id == k2.subject_id
    if "no " in k2.claim.lower() and "no " not in k1.claim.lower():
        return k1.subject_id == k2.subject_id
    return False


def reconstruct_knowledge_at(
    knowledge_timeline: List[TemporalKnowledge],
    point_in_time: datetime,
    person_id: str
) -> HistoricalKnowledgeSnapshot:
    """
    Reconstruct what was known at a specific point in time.
    This is the core "What did we know then?" capability.
    """
    point_in_time = ensure_utc(point_in_time)
    
    known = []
    uncertain = []
    conflicted = []
    
    for k in knowledge_timeline:
        if not k.is_valid_at(point_in_time):
            continue
        
        if k.status == KnowledgeStatus.SUPERSEDED:
            continue
        
        if k.status == KnowledgeStatus.CONFLICTED:
            conflicted.append({
                "knowledge_id": k.knowledge_id,
                "claim": k.claim,
                "confidence": k.confidence,
                "conflicting_with": k.provenance.get("conflicts", []),
            })
        elif k.confidence < 0.5:
            uncertain.append(k)
        else:
            known.append(k)
    
    # Find conflicts (multiple valid claims for same subject at this time)
    conflict_groups = _find_conflicts_at_time(known, point_in_time)
    for group in conflict_groups:
        for k in group:
            k.status = KnowledgeStatus.CONFLICTED
        known = [k for k in known if k not in group]
        conflicted.extend([{
            "knowledge_id": k.knowledge_id,
            "claim": k.claim,
            "confidence": k.confidence,
            "conflicting_with": [g.knowledge_id for g in group if g != k],
        } for k in group])
    
    # Unknown future: things that become known after this point
    unknown_future = []
    for k in knowledge_timeline:
        if k.valid_from > point_in_time and k.status != KnowledgeStatus.SUPERSEDED:
            unknown_future.append(f"{k.subject_type}:{k.subject_id} - {k.claim[:100]}")
    
    return HistoricalKnowledgeSnapshot(
        as_of=point_in_time,
        person_id=person_id,
        known_claims=known,
        uncertain_claims=uncertain,
        conflicting_claims=conflicted,
        unknown_future=unknown_future,
        provenance={
            "reconstructed_at": ensure_utc(datetime.now()).isoformat(),
            "total_knowledge_items": len(knowledge_timeline),
            "valid_at_time": len(known) + len(uncertain) + len(conflicted),
        }
    )


def _find_conflicts_at_time(
    knowledge: List[TemporalKnowledge],
    point_in_time: datetime
) -> List[List[TemporalKnowledge]]:
    """Find conflicting claims valid at the same time"""
    by_subject: Dict[str, List[TemporalKnowledge]] = defaultdict(list)
    for k in knowledge:
        if k.is_valid_at(point_in_time):
            key = f"{k.subject_type}:{k.subject_id}"
            by_subject[key].append(k)
    
    conflicts = []
    for items in by_subject.values():
        if len(items) > 1:
            # Check if they actually contradict
            for i in range(len(items)):
                for j in range(i+1, len(items)):
                    if _contradicts(items[i], items[j]):
                        conflicts.append(items)
                        break
    
    return conflicts


def query_historical_knowledge(
    knowledge_timeline: List[TemporalKnowledge],
    query: str,
    as_of: datetime,
    person_id: str
) -> Dict[str, Any]:
    """
    Query the historical knowledge base at a specific time.
    """
    snapshot = reconstruct_knowledge_at(knowledge_timeline, as_of, person_id)
    
    # Simple text search in claims
    query_lower = query.lower()
    
    matching_known = [k for k in snapshot.known_claims if query_lower in k.claim.lower()]
    matching_uncertain = [k for k in snapshot.uncertain_claims if query_lower in k.claim.lower()]
    matching_conflicts = [c for c in snapshot.conflicting_claims if query_lower in c["claim"].lower()]
    
    return {
        "query": query,
        "as_of": as_of.isoformat(),
        "known_matches": [{
            "knowledge_id": k.knowledge_id,
            "claim": k.claim,
            "confidence": k.confidence,
            "evidence_ids": k.evidence_ids,
            "valid_from": k.valid_from.isoformat(),
        } for k in matching_known],
        "uncertain_matches": [{
            "knowledge_id": k.knowledge_id,
            "claim": k.claim,
            "confidence": k.confidence,
        } for k in matching_uncertain],
        "conflicting_matches": matching_conflicts,
        "not_yet_known": any(query_lower in u.lower() for u in snapshot.unknown_future),
    }


def get_knowledge_evolution(
    knowledge_timeline: List[TemporalKnowledge],
    subject_type: str,
    subject_id: str
) -> List[Dict[str, Any]]:
    """
    Get the evolution of knowledge about a specific subject over time.
    """
    relevant = [
        k for k in knowledge_timeline
        if k.subject_type == subject_type and k.subject_id == subject_id
    ]
    relevant.sort(key=lambda k: k.valid_from)
    
    evolution = []
    for k in relevant:
        evolution.append({
            "knowledge_id": k.knowledge_id,
            "claim": k.claim,
            "confidence": k.confidence,
            "status": k.status.value,
            "valid_from": k.valid_from.isoformat(),
            "valid_until": k.valid_until.isoformat() if k.valid_until else None,
            "evidence_ids": k.evidence_ids,
            "source": k.source,
        })
    
    return evolution


def detect_knowledge_gaps(
    knowledge_timeline: List[TemporalKnowledge],
    person_id: str,
    expected_subjects: List[Dict[str, str]] = None,
    as_of: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """
    Detect gaps in knowledge - things we should know but don't.
    """
    as_of = as_of or ensure_utc(datetime.now())
    snapshot = reconstruct_knowledge_at(knowledge_timeline, as_of, person_id)
    
    gaps = []
    
    # Check expected subjects
    if expected_subjects:
        known_subjects = {f"{k.subject_type}:{k.subject_id}" for k in snapshot.known_claims}
        for exp in expected_subjects:
            key = f"{exp['subject_type']}:{exp['subject_id']}"
            if key not in known_subjects:
                gaps.append({
                    "gap_type": "missing_expected",
                    "subject_type": exp["subject_type"],
                    "subject_id": exp["subject_id"],
                    "description": f"Expected knowledge about {exp['subject_type']} {exp['subject_id']} not found",
                    "severity": "high" if exp.get("critical", False) else "medium",
                })
    
    # Check for uncertain claims that should be clarified
    for k in snapshot.uncertain_claims:
        if k.confidence < 0.3:
            gaps.append({
                "gap_type": "high_uncertainty",
                "knowledge_id": k.knowledge_id,
                "subject_type": k.subject_type,
                "subject_id": k.subject_id,
                "description": f"High uncertainty: {k.claim}",
                "severity": "medium",
            })
    
    # Check for conflicts
    for c in snapshot.conflicting_claims:
        gaps.append({
            "gap_type": "conflict",
            "knowledge_id": c["knowledge_id"],
            "description": f"Conflicting claims: {c['claim']}",
            "severity": "high",
        })
    
    return gaps


def compare_knowledge_at_times(
    knowledge_timeline: List[TemporalKnowledge],
    person_id: str,
    time_a: datetime,
    time_b: datetime
) -> Dict[str, Any]:
    """
    Compare what was known at two different times.
    """
    snapshot_a = reconstruct_knowledge_at(knowledge_timeline, time_a, person_id)
    snapshot_b = reconstruct_knowledge_at(knowledge_timeline, time_b, person_id)
    
    known_a = {f"{k.subject_type}:{k.subject_id}": k.claim for k in snapshot_a.known_claims}
    known_b = {f"{k.subject_type}:{k.subject_id}": k.claim for k in snapshot_b.known_claims}
    
    new_knowledge = {k: v for k, v in known_b.items() if k not in known_a}
    changed_knowledge = {}
    for k, v in known_b.items():
        if k in known_a and known_a[k] != v:
            changed_knowledge[k] = {"from": known_a[k], "to": v}
    removed_knowledge = {k: v for k, v in known_a.items() if k not in known_b}
    
    return {
        "time_a": time_a.isoformat(),
        "time_b": time_b.isoformat(),
        "new_knowledge": new_knowledge,
        "changed_knowledge": changed_knowledge,
        "removed_knowledge": removed_knowledge,
        "unchanged_knowledge": {k: v for k, v in known_a.items() if k in known_b and known_a[k] == v},
    }


# Export
__all__ = [
    "KnowledgeStatus",
    "TemporalKnowledge",
    "HistoricalKnowledgeSnapshot",
    "TemporalEvidence",
    "KnowledgeDependency",
    "ensure_utc",
    "build_knowledge_timeline",
    "reconstruct_knowledge_at",
    "query_historical_knowledge",
    "get_knowledge_evolution",
    "detect_knowledge_gaps",
    "compare_knowledge_at_times",
]


import uuid