"""Prepare consented improvement feedback for controlled review.

This is a staging pipeline, not an automatic model trainer. It applies basic
PII redaction, keeps examples pending human review, and leaves the source
record link in the database so withdrawal can remove queued examples.
"""
import re

from sqlalchemy.orm import Session

from app.models.collaboration import ConsentRecord
from app.models.improvement_training_example import ImprovementTrainingExample


_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{7,}\d)(?!\w)")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_SLASH_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_WHITESPACE = re.compile(r"\s+")


def redact_improvement_text(text: str) -> str:
    """Remove common direct identifiers before staging; this is not a guarantee of anonymity."""
    value = (text or "").strip()[:12000]
    value = _EMAIL.sub("[redacted email]", value)
    value = _URL.sub("[redacted link]", value)
    # Redact date formats before phone-like digit sequences so dates do not
    # get mislabeled as phone numbers.
    value = _ISO_DATE.sub("[redacted date]", value)
    value = _SLASH_DATE.sub("[redacted date]", value)
    value = _PHONE.sub("[redacted phone]", value)
    return _WHITESPACE.sub(" ", value).strip()


def stage_consented_feedback(
    db: Session,
    *,
    person_id: str,
    caregiver_id: str,
    category: str,
    detail: str,
    consent: ConsentRecord,
) -> ImprovementTrainingExample | None:
    """Stage an opted-in, redacted feedback example for review; never train automatically."""
    if (
        consent.person_id != person_id
        or consent.caregiver_id != caregiver_id
        or consent.revoked_at is not None
        or consent.data_improvement_consent is not True
    ):
        return None

    sample_text = redact_improvement_text(detail)
    if not sample_text:
        return None

    sample = ImprovementTrainingExample(
        person_id=person_id,
        caregiver_id=caregiver_id,
        category=(category or "unspecified")[:80],
        sample_text=sample_text,
        status="pending_review",
        consent_version=consent.accepted_terms_version,
        consented_at=consent.updated_at or consent.accepted_at,
    )
    db.add(sample)
    db.flush()
    return sample
