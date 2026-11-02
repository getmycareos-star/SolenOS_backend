import pytest
from fastapi import HTTPException

from app.api.care_continuity import _validate_links


@pytest.mark.parametrize(
    ("resource", "payload"),
    [
        (
            "relationships",
            {
                "source_type": "unregistered_resource",
                "source_id": "foreign-record-id",
                "target_type": "care_fact",
                "target_id": "another-record-id",
            },
        ),
        (
            "follow-up-links",
            {
                "source_type": "unknown",
                "source_id": "foreign-record-id",
                "target_type": "care_fact",
                "target_id": "another-record-id",
            },
        ),
    ],
)
def test_polymorphic_links_reject_unknown_resource_types(resource, payload):
    # Unknown types must be rejected before a database lookup. Previously,
    # non-strict resources silently skipped these links, allowing unverified IDs.
    with pytest.raises(HTTPException) as exc:
        _validate_links(db=None, person_id="person-a", resource=resource, payload=payload)

    assert exc.value.status_code == 422
    assert "Unsupported source_id type" in str(exc.value.detail)


def test_pattern_review_decision_requires_a_same_person_persisted_pattern(monkeypatch):
    from unittest.mock import Mock
    from sqlalchemy import Column, MetaData, String, Table

    from app.api import care_continuity

    patterns = Table(
        "patterns",
        MetaData(),
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
    )
    monkeypatch.setattr(care_continuity, "_table", lambda _db, name: patterns if name == "patterns" else None)

    db = Mock()
    db.execute.return_value.first.return_value = ("pattern-a",)
    care_continuity._validate_links(
        db=db,
        person_id="person-a",
        resource="review-decisions",
        payload={"target_type": "pattern", "target_id": "pattern-a"},
    )
    db.execute.assert_called_once()

    db.execute.return_value.first.return_value = None
    with pytest.raises(HTTPException) as exc:
        care_continuity._validate_links(
            db=db,
            person_id="person-a",
            resource="review-decisions",
            payload={"target_type": "pattern", "target_id": "pattern-b"},
        )
    assert exc.value.status_code == 404
    assert "not found in this care record" in str(exc.value.detail)


def test_temporal_relationship_review_decision_requires_a_same_person_link(monkeypatch):
    from unittest.mock import Mock
    from sqlalchemy import Column, MetaData, String, Table

    from app.api import care_continuity

    temporal_links = Table(
        "temporal_relationships",
        MetaData(),
        Column("id", String, primary_key=True),
        Column("person_id", String, nullable=False),
    )
    monkeypatch.setattr(
        care_continuity,
        "_table",
        lambda _db, name: temporal_links if name == "temporal_relationships" else None,
    )

    db = Mock()
    db.execute.return_value.first.return_value = ("temporal-link-a",)
    care_continuity._validate_links(
        db=db,
        person_id="person-a",
        resource="review-decisions",
        payload={"target_type": "temporal_relationship", "target_id": "temporal-link-a"},
    )
    db.execute.assert_called_once()

    db.execute.return_value.first.return_value = None
    with pytest.raises(HTTPException) as exc:
        care_continuity._validate_links(
            db=db,
            person_id="person-a",
            resource="review-decisions",
            payload={"target_type": "temporal_relationship", "target_id": "temporal-link-foreign"},
        )
    assert exc.value.status_code == 404
    assert "not found in this care record" in str(exc.value.detail)
