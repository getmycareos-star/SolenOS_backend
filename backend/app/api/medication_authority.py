import hashlib
import hmac
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.care import Evidence, Caregiver
from app.models.medication_authority import MedicationAuthorityVerification
from app.schemas.medication_authority import MedicationAuthorityVerificationCreate

router = APIRouter(prefix="/medication-authority", tags=["medication-safety"])
ALLOWED_ROLES = {"physician", "licensed_prescriber", "prescriber"}
ALLOWED_METHODS = {"signed_prescription", "provider_portal", "direct_provider_confirmation"}


@router.post("/verify", status_code=201)
def verify_medication_source(
    payload: MedicationAuthorityVerificationCreate,
    request: Request,
    x_medication_authority_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    expected_token = os.getenv("MEDICATION_AUTHORITY_REVIEW_TOKEN", "").strip()
    if not expected_token:
        raise HTTPException(status_code=503, detail="Trusted medication-source verification is not configured")
    if not x_medication_authority_token or not hmac.compare_digest(x_medication_authority_token, expected_token):
        raise HTTPException(status_code=403, detail="Trusted verification credential is invalid")

    person_id = getattr(request.state, "scoped_person_id", None)
    caregiver_id = getattr(request.state, "scoped_caregiver_id", None)
    if not person_id or not caregiver_id:
        raise HTTPException(status_code=401, detail="An authenticated caregiver session is required")
    if str(payload.person_id) != str(person_id):
        raise HTTPException(status_code=403, detail="This session is not authorised for that care record")
    caregiver = db.query(Caregiver).filter(Caregiver.id == caregiver_id, Caregiver.person_id == person_id).first()
    if caregiver is None:
        raise HTTPException(status_code=403, detail="Caregiver is not linked to this care record")
    evidence = db.query(Evidence).filter(Evidence.id == payload.evidence_id, Evidence.person_id == person_id).first()
    if evidence is None:
        raise HTTPException(status_code=404, detail="Source evidence not found in this care record")
    if not (evidence.source_text or "").strip():
        raise HTTPException(status_code=422, detail="Empty source evidence cannot be verified")
    role = payload.authority_role.strip().lower()
    method = payload.verification_method.strip().lower()
    if role not in ALLOWED_ROLES or method not in ALLOWED_METHODS:
        raise HTTPException(status_code=422, detail="Medication source must be verified against an authorised prescriber and an approved verification method")
    expiry = payload.expires_at.astimezone(timezone.utc) if payload.expires_at else None
    if expiry is not None and expiry <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="Verification expiry must be in the future")

    record = db.query(MedicationAuthorityVerification).filter_by(evidence_id=evidence.id).first()
    values = {
        "person_id": person_id,
        "source_text_sha256": hashlib.sha256(evidence.source_text.encode("utf-8")).hexdigest(),
        "authority_role": role,
        "verifier_identity": payload.verifier_identity.strip(),
        "verification_method": method,
        "verified_by_caregiver_id": caregiver_id,
        "verified_at": datetime.now(timezone.utc),
        "expires_at": expiry,
    }
    if record is None:
        record = MedicationAuthorityVerification(evidence_id=evidence.id, **values)
        db.add(record)
    else:
        for key, value in values.items():
            setattr(record, key, value)
    db.commit()
    return {
        "evidence_id": evidence.id,
        "person_id": person_id,
        "authority_role": record.authority_role,
        "verification_method": record.verification_method,
        "verified_at": record.verified_at,
        "expires_at": record.expires_at,
        "source_digest": record.source_text_sha256,
    }
