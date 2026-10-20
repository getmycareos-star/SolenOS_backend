from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import logging
from app.core.database import get_db
from app.models.caregiver_intelligence import (    CaregiverRole as CaregiverRoleModel,    CaregiverPerspective as CaregiverPerspectiveModel,    CaregiverCommunication as CaregiverCommunicationModel,)
from app.schemas.caregiver_intelligence import (    CaregiverProfileCreate,    CaregiverProfile,    CaregiverRoleCreate,    CaregiverRole,    CaregiverResponsibilityCreate,    CaregiverResponsibility,    CaregiverObservationCreate,    CaregiverObservation,    CaregiverPerspectiveCreate,    CaregiverPerspective,    CaregiverHandoffCreate,    CaregiverHandoff,    CaregiverActivityCreate,    CaregiverActivity,    CaregiverCommunicationCreate,    CaregiverCommunication,    CareNetworkRelationshipCreate,    CareNetworkRelationship,    ResponsibilityGapCreate,    ResponsibilityGap,    PerspectiveConflictCreate,    PerspectiveConflict,    DuplicateActionCreate,    DuplicateAction,    CaregiverContextCreate,    CaregiverContext,    CaregiverSummary,    MultiCaregiverSummary,)
from app.services.caregiver_intelligence import CaregiverIntelligenceService
from app.core.caregiver_enums import (    CaregiverInvolvementStatus,    HandoffAcceptanceStatus,    CaregiverActivityType,)
# Cache imports
from app.core.cache import cache_service
from app.core.cache_key_utils import CacheKeyPrefix, CacheTTL
from app.core.cache import cached_response
router = APIRouter()





