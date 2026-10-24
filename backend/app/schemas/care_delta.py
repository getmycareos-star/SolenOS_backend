from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class CareDeltaReconcileRequest(BaseModel):
    person_id: str = Field(min_length=1, max_length=160)
    event_date: Optional[datetime] = None
    source: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=100000)
    source_event_id: Optional[str] = Field(default=None, max_length=160)
    evidence_ids: List[str] = Field(default_factory=list, max_length=100)
    persist: bool = True
    created_by_caregiver_id: Optional[str] = None
