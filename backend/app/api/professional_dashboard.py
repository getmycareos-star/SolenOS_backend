"""Professional Dashboard API endpoints for SolenOS.

Implements endpoints for professional caregiver dashboard.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.core.database import get_db
from app.core.security import (
    get_principal_from_request,
    require_authenticated,
    require_care_context_access,
    AuthenticatedPrincipal
)
from app.models.identity import Account, ProfessionalProfile
from app.schemas.professional_dashboard import (
    ProfessionalDashboard,
    ProfessionalDashboardAlert
)
from app.services.professional_dashboard import ProfessionalDashboardService

router = APIRouter(prefix="/professional-dashboard", tags=["professional-dashboard"])


@router.get("", response_model=ProfessionalDashboard)
def get_professional_dashboard(
    request: Request,
    timeframe_days: Optional[int] = Query(7, ge=1, le=365, description="Number of days to look back for data"),
    db: Session = Depends(get_db),
):
    """Get the professional dashboard for the authenticated professional."""
    principal = require_authenticated(get_principal_from_request(request))
    
    # Verify the user is a professional
    professional_profile = db.query(ProfessionalProfile).filter(
        ProfessionalProfile.account_id == principal.account_id
    ).first()
    
    if not professional_profile:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not authorized as a professional caregiver"
        )
    
    # Additional check: verify the professional is verified
    if professional_profile.verification_status not in ["verified", "VERIFIED"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Professional profile is not verified"
        )
    
    service = ProfessionalDashboardService(db)
    dashboard = service.generate_professional_dashboard(
        professional_account_id=principal.account_id,
        timeframe_days=timeframe_days or 7
    )
    
    return dashboard


@router.get("/alerts", response_model=List[ProfessionalDashboardAlert])
def get_professional_alerts(
    request: Request,
    severity: Optional[str] = Query(None, description="Filter by severity (low, medium, high, critical)"),
    alert_type: Optional[str] = Query(None, description="Filter by alert type"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of alerts to return"),
    db: Session = Depends(get_db),
):
    """Get alerts for the professional dashboard."""
    principal = require_authenticated(get_principal_from_request(request))
    
    # Verify the user is a professional
    professional_profile = db.query(ProfessionalProfile).filter(
        ProfessionalProfile.account_id == principal.account_id
    ).first()
    
    if not professional_profile:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not authorized as a professional caregiver"
        )
    
    # For now, we'll generate the dashboard and filter the alerts
    # In a more optimized implementation, we'd query alerts directly
    service = ProfessionalDashboardService(db)
    dashboard = service.generate_professional_dashboard(
        professional_account_id=principal.account_id,
        timeframe_days=7
    )
    
    alerts = dashboard.alerts
    
    # Apply filters
    if severity:
        alerts = [alert for alert in alerts if alert.severity.lower() == severity.lower()]
    
    if alert_type:
        try:
            alert_enum = ProfessionalDashboardAlert(alert_type)
            alerts = [alert for alert in alerts if alert.alert_type == alert_enum]
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid alert type: {alert_type}"
            )
    
    # Limit results
    alerts = alerts[:limit]
    
    return alerts


@router.get("/patients/{person_id}/summary", response_model=dict)
def get_patient_summary_for_professional(
    person_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get a summary of a specific patient for the professional dashboard."""
    principal = require_authenticated(get_principal_from_request(request))
    
    # Verify the user is a professional
    professional_profile = db.query(ProfessionalProfile).filter(
        ProfessionalProfile.account_id == principal.account_id
    ).first()
    
    if not professional_profile:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not authorized as a professional caregiver"
        )
    
    # Verify the professional is authorized to care for this person
    authorization = db.query(ProfessionalRoleLink).join(ProfessionalProfile).filter(
        and_(
            ProfessionalRoleLink.professional_profile_id == professional_profile.id,
            ProfessionalRoleLink.person_id == person_id,
            ProfessionalRoleLink.is_active == 1,
            ProfessionalProfile.verification_status.in_([
                "verified", 
                "VERIFIED"
            ])
        )
    ).first()
    
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this patient's information"
        )
    
    # Verify the person exists
    person = db.query(Person).filter(Person.id == person_id).first()
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    
    # Generate summary for this specific person
    service = ProfessionalDashboardService(db)
    patient_summary, alerts = service._generate_person_summary_and_alerts(
        person, professional_profile, utc_now() - timedelta(days=7)
    )
    
    return {
        "patient_summary": patient_summary.dict(),
        "recent_alerts": [alert.dict() for alert in alerts[:10]]  # Return top 10 alerts
    }