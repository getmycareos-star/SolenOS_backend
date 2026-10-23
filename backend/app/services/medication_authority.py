import hashlib
import re
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.care import Evidence
from app.models.medication_authority import MedicationAuthorityVerification

TRUSTED_PRESCRIBER_ROLES = {"physician", "licensed_prescriber", "prescriber"}
VERIFICATION_METHODS = {"signed_prescription", "provider_portal", "direct_provider_confirmation"}
MEDICATION_INSTRUCTION_PATTERN = re.compile(
    r"\b(?:medication|medicine|drug|tablet|capsule|dose|dosage|mg|mcg|ml|insulin)\b"
    r".{0,100}\b(?:start|stop|continue|take|administer|increase|decrease|hold|withhold|reduce|switch|prescribe|give)\b"
    r"|\b(?:start|stop|continue|take|administer|increase|decrease|hold|withhold|reduce|switch|prescribe|give)\b"
    r".{0,100}\b(?:medication|medicine|drug|tablet|capsule|dose|dosage|mg|mcg|ml|insulin)\b",
    re.IGNORECASE | re.DOTALL,
)


def medication_instruction_requires_authority(text: str, is_medication_action: bool = False) -> bool:
    return bool(is_medication_action or MEDICATION_INSTRUCTION_PATTERN.search(text or ""))


def get_verified_medication_authority(
    db: Session,
    *,
    person_id: str,
    evidence_id: str,
    source_authority: str,
):
    if (source_authority or "").strip().lower() not in TRUSTED_PRESCRIBER_ROLES:
        return None
    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.person_id == person_id,
    ).first()
    if evidence is None:
        return None
    verification = db.query(MedicationAuthorityVerification).filter(
        MedicationAuthorityVerification.evidence_id == evidence_id,
        MedicationAuthorityVerification.person_id == person_id,
        MedicationAuthorityVerification.authority_role.in_(TRUSTED_PRESCRIBER_ROLES),
        MedicationAuthorityVerification.verification_method.in_(VERIFICATION_METHODS),
        MedicationAuthorityVerification.source_text_sha256 == hashlib.sha256(
            (evidence.source_text or "").encode("utf-8")
        ).hexdigest(),
    ).first()
    if verification is None:
        return None
    now = datetime.now(timezone.utc)
    expiry = verification.expires_at
    if expiry is not None:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= now:
            return None
    return verification
