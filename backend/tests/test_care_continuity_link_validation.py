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
