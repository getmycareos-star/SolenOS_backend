from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


class MedicationAuthorityVerificationCreate(BaseModel):
    evidence_id: str = Field(min_length=1)
    authority_role: Literal["physician", "licensed_prescriber", "prescriber"]
    verifier_identity: str = Field(min_length=3, max_length=200)
    verification_method: Literal["signed_prescription", "provider_portal", "direct_provider_confirmation"]
    expires_at: Optional[datetime] = None

    @field_validator("expires_at")
    @classmethod
    def require_aware_expiry(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("expires_at must include a timezone offset")
        return value
