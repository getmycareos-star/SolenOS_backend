from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List

from app.core.database import get_db
from app.core.security import (
    get_principal_from_request,
    require_team_access,
    add_security_headers,
    _log_security_event,
)
from app.models.community import (
    CommunityPost, CommunityPostStatus,
)
from app.models.identity import Account
from app.schemas.community import (
    TeamDashboard, StateReport,
)
from app.services.community import CommunityService

team_router = APIRouter(prefix="/team", tags=["team"])


@team_router.get("/dashboard")
def get_team_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get team dashboard metrics (operations-only)."""
    principal = require_team_access(get_principal_from_request(request))
    service = CommunityService(db)
    return service.compute_team_dashboard()


@team_router.get("/state-report")
def get_state_report(
    request: Request,
    db: Session = Depends(get_db),
):
    """Get state of dementia care report (operations-only)."""
    principal = require_team_access(get_principal_from_request(request))
    service = CommunityService(db)
    return service.compute_state_report()