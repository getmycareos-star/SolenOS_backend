"""Professional Dashboard schemas for SolenOS.

Defines the request/response models for Professional Dashboard endpoints.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class ProfessionalDashboardAlert(str, Enum):
    """Types of alerts for professional dashboard."""
    NEW_SYMPTOM = "new_symptom"
    SYMPTOM_WORSENING = "symptom_worsening"
    MEDICATION_CHANGE = "medication_change"
    APPOINTMENT_MISSED = "appointment_missed"
    APPOINTMENT_UPCOMING = "appointment_upcoming"
    CAREGIVER_CONCERN = "caregiver_concern"
    BEHAVIOR_CHANGE = "behavior_change"
    COGNITIVE_CHANGE = "cognitive_change"
    FUNCTIONAL_CHANGE = "functional_change"


class ProfessionalDashboardPersonSummary(BaseModel):
    """Summary of a person for the professional dashboard."""
    person_id: str
    person_name: str
    primary_condition: Optional[str] = None
    last_updated: datetime
    alert_count: int = 0
    alerts: List[ProfessionalDashboardAlert] = []
    care_team_size: int = 0
    has_recent_updates: bool = False
    next_appointment: Optional[datetime] = None
    compliance_score: Optional[float] = None  # 0-100 scale


class ProfessionalDashboardAlertItem(BaseModel):
    """An alert item for the professional dashboard."""
    person_id: str
    person_name: str
    alert_type: ProfessionalDashboardAlert
    title: str
    description: str
    severity: str  # low, medium, high, critical
    timestamp: datetime
    action_required: bool = False
    suggested_action: Optional[str] = None


class ProfessionalDashboard(BaseModel):
    """Professional dashboard showing information for professionals caring for people."""
    # Summary statistics
    total_patients: int
    active_patients_today: int
    patients_with_alerts: int
    total_alerts: int
    
    # Alerts requiring attention
    alerts: List[ProfessionalDashboardAlertItem] = []
    
    # Patient summaries
    patients: List[ProfessionalDashboardPersonSummary] = []
    
    # Professional's own info
    professional_name: str
    professional_role: str
    specialty: Optional[str] = None
    
    # Timeframe
    generated_at: datetime
    timeframe_days: int = 7  # Default to last 7 days
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }