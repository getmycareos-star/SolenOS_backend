"""Resolve the private caregiver-profile identity from server session identity.

CaregiverProfile IDs and caregivers IDs are separate tables and must never be
assumed equal. This service creates/links a profile only from a validated
server-side caregiver/person pair; callers must not supply the link ID.
"""
from sqlalchemy.orm import Session

from app.models.care import Caregiver
from app.models.caregiver_intelligence import CaregiverProfile


def ensure_caregiver_profile_for_session(
    db: Session,
    *,
    person_id: str,
    session_caregiver_id: str,
    name: str | None = None,
    email: str | None = None,
    relationship: str | None = None,
) -> CaregiverProfile:
    caregiver = (
        db.query(Caregiver)
        .filter(Caregiver.id == str(session_caregiver_id), Caregiver.person_id == str(person_id))
        .first()
    )
    if caregiver is None:
        raise ValueError("Session caregiver is not linked to the requested care record")

    profile = (
        db.query(CaregiverProfile)
        .filter(CaregiverProfile.session_caregiver_id == str(session_caregiver_id))
        .first()
    )
    if profile is not None:
        if str(profile.person_id) != str(person_id):
            raise ValueError("Caregiver profile identity link crosses care records")
        if email:
            if profile.email and profile.email.casefold() != email.casefold():
                raise ValueError("Caregiver profile already has a different email identity")
            existing_email = (
                db.query(CaregiverProfile)
                .filter(CaregiverProfile.email == email, CaregiverProfile.id != profile.id)
                .first()
            )
            if existing_email is not None:
                raise ValueError("Email is already associated with another caregiver profile")
            profile.email = email
        db.flush()
        return profile

    if email:
        profile = db.query(CaregiverProfile).filter(CaregiverProfile.email == email).first()
        if profile is not None:
            if str(profile.person_id) != str(person_id):
                raise ValueError("Email is already associated with another care record")
            if profile.session_caregiver_id and str(profile.session_caregiver_id) != str(session_caregiver_id):
                raise ValueError("Caregiver profile is already linked to another session identity")
            profile.session_caregiver_id = str(session_caregiver_id)
            if name and not profile.name:
                profile.name = name
            db.flush()
            return profile

    profile = CaregiverProfile(
        person_id=str(person_id),
        session_caregiver_id=str(session_caregiver_id),
        name=(name or caregiver.name or "Caregiver").strip() or "Caregiver",
        email=email,
        relationship=(relationship or caregiver.relationship or "Family").strip() or "Family",
    )
    db.add(profile)
    db.flush()
    return profile
