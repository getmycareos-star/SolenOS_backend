"""Governed dementia knowledge catalogue and publication gate.

This module deliberately separates source discovery from publishable caregiver
guidance. A source being authoritative does not make every statement on it
appropriate for SolenOS. Knowledge items remain unavailable to runtime retrieval
until a qualified reviewer has approved their exact content and applicability.

The catalogue is version-controlled product metadata. It is not a substitute for
the planned PostgreSQL-backed knowledge lifecycle or clinical review programme.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
import re
from typing import Iterable


KNOWLEDGE_CATALOG_VERSION = "1.0.0"


class SourceStatus(StrEnum):
    CATALOGUED = "catalogued_not_ingested"
    INGESTED = "ingested_not_reviewed"
    REVIEWED = "reviewed"
    RETIRED = "retired"


class EvidenceClass(StrEnum):
    CLINICAL_GUIDELINE = "clinical_guideline"
    PUBLIC_HEALTH_GUIDANCE = "public_health_guidance"
    CAREGIVER_EDUCATION = "caregiver_education"
    RESEARCH = "research"


class KnowledgeStatus(StrEnum):
    DRAFT = "draft"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    source_id: str
    publisher: str
    title: str
    url: str
    evidence_class: EvidenceClass
    jurisdiction: str
    published_on: date | None
    last_reviewed_on: date | None
    status: SourceStatus
    scope: str


@dataclass(frozen=True, slots=True)
class DementiaDomain:
    domain_id: str
    title: str
    description: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeItem:
    item_id: str
    domain_id: str
    title: str
    content: str
    source_ids: tuple[str, ...]
    evidence_class: EvidenceClass
    jurisdiction: str
    applicable_context: str
    limitations: str
    reviewed_by: str | None
    reviewed_on: date | None
    review_due_on: date | None
    status: KnowledgeStatus


SOURCES: tuple[KnowledgeSource, ...] = (
    KnowledgeSource(
        source_id="nia_dementia",
        publisher="U.S. National Institute on Aging",
        title="Alzheimer's Disease and Related Dementias",
        url="https://www.nia.nih.gov/health/alzheimers-and-dementia",
        evidence_class=EvidenceClass.PUBLIC_HEALTH_GUIDANCE,
        jurisdiction="United States",
        published_on=None,
        last_reviewed_on=None,
        status=SourceStatus.CATALOGUED,
        scope="Dementia types, symptoms, diagnosis, treatment context and caregiving.",
    ),
    KnowledgeSource(
        source_id="alz_association",
        publisher="Alzheimer's Association",
        title="Alzheimer's Association",
        url="https://www.alz.org/",
        evidence_class=EvidenceClass.CAREGIVER_EDUCATION,
        jurisdiction="United States",
        published_on=None,
        last_reviewed_on=None,
        status=SourceStatus.CATALOGUED,
        scope="Family caregiving education, safety, communication and support.",
    ),
    KnowledgeSource(
        source_id="who_isupport",
        publisher="World Health Organization",
        title="iSupport for Dementia: Training and support manual for carers",
        url="https://www.who.int/publications/i/item/9789241515863",
        evidence_class=EvidenceClass.PUBLIC_HEALTH_GUIDANCE,
        jurisdiction="International",
        published_on=date(2019, 5, 9),
        last_reviewed_on=None,
        status=SourceStatus.CATALOGUED,
        scope="Dementia fundamentals, caregiver wellbeing, everyday care and behaviour changes.",
    ),
    KnowledgeSource(
        source_id="nice_ng97",
        publisher="National Institute for Health and Care Excellence",
        title="Dementia: assessment, management and support (NG97)",
        url="https://www.nice.org.uk/guidance/ng97",
        evidence_class=EvidenceClass.CLINICAL_GUIDELINE,
        jurisdiction="United Kingdom",
        published_on=date(2018, 6, 20),
        last_reviewed_on=date(2025, 10, 24),
        status=SourceStatus.CATALOGUED,
        scope="Clinical guideline covering assessment, person-centred care, coordination, interventions and carer support.",
    ),
)


DOMAINS: tuple[DementiaDomain, ...] = (
    DementiaDomain("fundamentals", "Dementia fundamentals", "Types, terminology, diagnosis boundaries and general course.", ("nia_dementia", "who_isupport", "nice_ng97")),
    DementiaDomain("cognition", "Cognitive changes", "Memory, orientation, language, attention and recognition.", ("nia_dementia", "nice_ng97")),
    DementiaDomain("behaviour", "Behavioural and psychological changes", "Distress, agitation, withdrawal, suspicion and repetitive behaviour.", ("alz_association", "who_isupport", "nice_ng97")),
    DementiaDomain("communication", "Communication", "Listening, accessible language, reassurance and non-verbal communication.", ("alz_association", "who_isupport")),
    DementiaDomain("personal_care", "Personal care", "Bathing, dressing, grooming, toileting, autonomy and dignity.", ("alz_association", "who_isupport")),
    DementiaDomain("nutrition", "Nutrition and swallowing", "Eating, appetite, swallowing concerns and weight-related observations.", ("nia_dementia", "alz_association", "nice_ng97")),
    DementiaDomain("hydration", "Hydration", "Drinking patterns, reported refusal and hydration-related concerns.", ("nia_dementia", "alz_association")),
    DementiaDomain("sleep", "Sleep", "Night waking, routine changes and sleep-related concerns.", ("nia_dementia", "alz_association")),
    DementiaDomain("mobility", "Mobility and falls", "Mobility baseline, falls, balance and functional support.", ("nia_dementia", "nice_ng97")),
    DementiaDomain("safety", "Safety and supervision", "Wandering, driving, household safety, financial vulnerability and supervision.", ("alz_association", "nia_dementia")),
    DementiaDomain("medical", "Medical considerations", "Medication information boundaries, pain, acute changes and professional assessment.", ("nia_dementia", "nice_ng97")),
    DementiaDomain("emotional_wellbeing", "Emotional wellbeing", "Fear, anxiety, frustration, loneliness, identity and meaningful activity.", ("who_isupport", "alz_association", "nice_ng97")),
    DementiaDomain("caregiver_wellbeing", "Caregiver wellbeing", "Caregiver strain, respite, family responsibilities and support.", ("who_isupport", "alz_association", "nice_ng97")),
    DementiaDomain("long_term_care", "Long-term care and transitions", "Care coordination, transitions, advance preferences and future planning.", ("nia_dementia", "nice_ng97")),
)


SOURCE_BY_ID = {source.source_id: source for source in SOURCES}
DOMAIN_BY_ID = {domain.domain_id: domain for domain in DOMAINS}


def validate_knowledge_item(item: KnowledgeItem) -> tuple[str, ...]:
    """Return governance defects; never silently repair submitted metadata."""
    defects: list[str] = []
    if item.domain_id not in DOMAIN_BY_ID:
        defects.append("unknown_domain")
    if not item.source_ids:
        defects.append("missing_sources")
    elif any(source_id not in SOURCE_BY_ID for source_id in item.source_ids):
        defects.append("unknown_source")
    if not item.title.strip() or not item.content.strip():
        defects.append("empty_content")
    if item.status == KnowledgeStatus.APPROVED:
        if any(
            SOURCE_BY_ID[source_id].jurisdiction not in (item.jurisdiction, "International")
            for source_id in item.source_ids
            if source_id in SOURCE_BY_ID
        ):
            defects.append("source_jurisdiction_mismatch")
        if any(
            SOURCE_BY_ID[source_id].status != SourceStatus.REVIEWED
            for source_id in item.source_ids
            if source_id in SOURCE_BY_ID
        ):
            defects.append("source_not_reviewed")
        if not item.reviewed_by or not item.reviewed_on:
            defects.append("approval_without_reviewer")
        if not item.review_due_on:
            defects.append("approval_without_review_due_date")
        if not item.limitations.strip():
            defects.append("approval_without_limitations")
        if not item.applicable_context.strip():
            defects.append("approval_without_applicability")
    return tuple(defects)


def search_approved_knowledge(
    query: str,
    items: Iterable[KnowledgeItem],
    *,
    as_of: date,
    domain_id: str | None = None,
    limit: int = 5,
) -> tuple[KnowledgeItem, ...]:
    """Search only approved, in-review-date knowledge with valid provenance.

    No model inference, diagnosis, urgency classification or patient-specific
    interpretation occurs here. Callers must separately authorize and retrieve
    person-specific LCR evidence.
    """
    if limit < 1:
        return ()
    if domain_id is not None and domain_id not in DOMAIN_BY_ID:
        return ()
    terms = {term for term in re.findall(r"[a-z0-9]+", query.casefold()) if len(term) > 2}
    if not terms:
        return ()

    ranked: list[tuple[int, str, KnowledgeItem]] = []
    for item in items:
        if item.status != KnowledgeStatus.APPROVED:
            continue
        if domain_id is not None and item.domain_id != domain_id:
            continue
        if item.review_due_on is None or item.review_due_on < as_of:
            continue
        if validate_knowledge_item(item):
            continue
        haystack = " ".join((item.title, item.content, item.applicable_context)).casefold()
        score = sum(1 for term in terms if term in haystack)
        if score:
            ranked.append((score, item.item_id, item))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return tuple(row[2] for row in ranked[:limit])
