from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.evidence_enums import EvidenceType


class LongitudinalEvidenceEvent(BaseModel):
    id: str
    person_id: str
    evidence_id: str
    event_type: str
    event_category: str
    changed_fields: Optional[List[str]] = None
    old_values: Optional[Dict[str, Any]] = None
    new_values: Optional[Dict[str, Any]] = None
    triggered_by: Optional[str] = None
    trigger_details: Optional[str] = None
    downstream_impact_count: int = 0
    affected_entities: Optional[List[str]] = None
    event_time: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class LongitudinalEvidenceTrend(BaseModel):
    id: str
    person_id: str
    evidence_type: EvidenceType
    window_start: datetime
    window_end: datetime
    window_type: str
    count_trend: Optional[str] = None
    count_change_pct: Optional[float] = None
    confidence_trend: Optional[str] = None
    avg_confidence_change: Optional[float] = None
    status_change_count: int = 0
    relationship_change_count: int = 0
    correction_count: int = 0
    invalidation_count: int = 0
    first_evidence_id: Optional[str] = None
    last_evidence_id: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    computed_at: datetime
    data_points: int = 0

    class Config:
        from_attributes = True