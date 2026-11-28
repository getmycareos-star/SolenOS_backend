from dataclasses import replace
from datetime import date

import pytest

import app.services.dementia_knowledge as knowledge
from app.services.dementia_knowledge import (
    DOMAINS,
    KNOWLEDGE_CATALOG_VERSION,
    SOURCES,
    EvidenceClass,
    KnowledgeItem,
    KnowledgeStatus,
    SourceStatus,
    search_approved_knowledge,
    validate_knowledge_item,
)


@pytest.fixture(autouse=True)
def _review_test_source(monkeypatch):
    source = knowledge.SOURCE_BY_ID["who_isupport"]
    monkeypatch.setitem(
        knowledge.SOURCE_BY_ID,
        "who_isupport",
        replace(
            source,
            status=SourceStatus.REVIEWED,
            last_reviewed_on=date(2026, 1, 1),
        ),
    )


def _item(**overrides):
    values = {
        "item_id": "communication-001",
        "domain_id": "communication",
        "title": "Communication approach",
        "content": "Use a calm, simple communication approach and allow time to respond.",
        "source_ids": ("who_isupport",),
        "evidence_class": EvidenceClass.CAREGIVER_EDUCATION,
        "jurisdiction": "United States",
        "applicable_context": "General caregiver education; adapt to the person's preferences.",
        "limitations": "Not an assessment or clinical recommendation.",
        "reviewed_by": "qualified-reviewer",
        "reviewed_on": date(2026, 1, 1),
        "review_due_on": date(2027, 1, 1),
        "status": KnowledgeStatus.APPROVED,
    }
    values.update(overrides)
    return KnowledgeItem(**values)


def test_catalog_covers_all_fourteen_dementia_domains_and_known_sources():
    assert KNOWLEDGE_CATALOG_VERSION
    assert len(DOMAINS) == 14
    source_ids = {source.source_id for source in SOURCES}
    assert {"nia_dementia", "alz_association", "who_isupport", "nice_ng97"} <= source_ids
    assert all(domain.source_ids for domain in DOMAINS)
    assert all(set(domain.source_ids) <= source_ids for domain in DOMAINS)


def test_source_catalog_does_not_claim_sources_have_been_ingested_or_reviewed():
    assert all(source.status == SourceStatus.CATALOGUED for source in SOURCES)


def test_knowledge_retrieval_excludes_drafts_and_items_past_review_date():
    approved = _item()
    draft = _item(item_id="draft", status=KnowledgeStatus.DRAFT)
    overdue = _item(item_id="overdue", review_due_on=date(2025, 12, 31))
    results = search_approved_knowledge(
        "communication calm",
        [approved, draft, overdue],
        as_of=date(2026, 1, 1),
    )
    assert results == (approved,)


def test_knowledge_retrieval_requires_valid_provenance_and_review_metadata():
    invalid = _item(reviewed_by=None, review_due_on=None, source_ids=())
    assert "missing_sources" in validate_knowledge_item(invalid)
    assert "approval_without_reviewer" in validate_knowledge_item(invalid)
    assert "approval_without_review_due_date" in validate_knowledge_item(invalid)
    assert search_approved_knowledge(
        "communication", [invalid], as_of=date(2026, 1, 1)
    ) == ()


def test_knowledge_retrieval_is_domain_scoped_and_rejects_unknown_domains():
    communication = _item()
    result = search_approved_knowledge(
        "communication calm",
        [communication],
        as_of=date(2026, 1, 1),
        domain_id="communication",
    )
    assert result == (communication,)
    assert search_approved_knowledge(
        "communication calm",
        [communication],
        as_of=date(2026, 1, 1),
        domain_id="not-a-domain",
    ) == ()


def test_knowledge_retrieval_returns_nothing_for_empty_or_unusable_queries():
    item = _item()
    assert search_approved_knowledge("", [item], as_of=date(2026, 1, 1)) == ()
    assert search_approved_knowledge("x y", [item], as_of=date(2026, 1, 1)) == ()
    assert search_approved_knowledge(
        "communication", [item], as_of=date(2026, 1, 1), limit=0
    ) == ()


def test_approved_knowledge_is_blocked_when_its_source_is_only_catalogued(monkeypatch):
    source = knowledge.SOURCE_BY_ID["who_isupport"]
    monkeypatch.setitem(
        knowledge.SOURCE_BY_ID,
        "who_isupport",
        replace(source, status=SourceStatus.CATALOGUED),
    )
    item = _item()
    assert "source_not_reviewed" in validate_knowledge_item(item)
    assert search_approved_knowledge(
        "communication calm", [item], as_of=date(2026, 1, 1)
    ) == ()


def test_uk_guideline_cannot_be_published_as_us_specific_guidance(monkeypatch):
    source = knowledge.SOURCE_BY_ID["nice_ng97"]
    monkeypatch.setitem(
        knowledge.SOURCE_BY_ID,
        "nice_ng97",
        replace(
            source,
            status=SourceStatus.REVIEWED,
            last_reviewed_on=date(2026, 1, 1),
        ),
    )
    item = _item(source_ids=("nice_ng97",))
    assert "source_jurisdiction_mismatch" in validate_knowledge_item(item)
    assert search_approved_knowledge(
        "communication calm", [item], as_of=date(2026, 1, 1)
    ) == ()


def test_future_dated_knowledge_review_is_not_available_yet():
    future = _item(reviewed_on=date(2026, 1, 2))
    assert search_approved_knowledge(
        "communication calm", [future], as_of=date(2026, 1, 1)
    ) == ()


def test_companion_knowledge_provenance_exposes_source_links_without_copying_education_text():
    from app.services.dementia_knowledge import build_companion_knowledge_provenance

    result = build_companion_knowledge_provenance([{
        "item_id": "item-1",
        "title": "Communication",
        "domain_id": "communication",
        "content": "This full educational content must not be copied into message metadata.",
        "evidence_class": "caregiver_education",
        "reviewed_at": "2026-11-01T12:00:00+00:00",
        "review_due_on": "2027-11-01",
        "sources": [{
            "publisher": "WHO",
            "title": "iSupport for Dementia",
            "url": "https://www.who.int/publications/i/item/9789241515863",
            "source_revision_id": "revision-1",
            "content_sha256": "a" * 64,
            "source_locator": "Module 2",
        }],
    }], status="available")
    assert result["status"] == "available"
    assert result["items"][0]["sources"][0]["publisher"] == "WHO"
    assert result["items"][0]["sources"][0]["source_revision_id"] == "revision-1"
    assert "content" not in result["items"][0]
    assert "must not be copied" not in str(result)
    assert build_companion_knowledge_provenance([], status="available") == {
        "status": "no_approved_match", "items": []
    }
