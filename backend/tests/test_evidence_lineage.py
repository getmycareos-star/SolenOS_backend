from types import SimpleNamespace

from app.services.evidence_lineage import build_evidence_lineage


def test_parent_evidence_is_shared_origin_even_when_parent_is_not_selected():
    child = SimpleNamespace(id="child", parent_evidence_id="original", original_source_reference=None)
    lineage, independence = build_evidence_lineage([child], [], ["child"])
    assert lineage["child"] == "persisted-lineage:child"
    assert independence["child"] == "shared_origin"


def test_matching_explicit_source_reference_groups_records_without_text_similarity():
    rows = [
        SimpleNamespace(id="a", parent_evidence_id=None, original_source_reference="document://care-plan/7"),
        SimpleNamespace(id="b", parent_evidence_id=None, original_source_reference=" DOCUMENT://CARE-PLAN/7 "),
    ]
    lineage, independence = build_evidence_lineage(rows, [], ["a", "b"])
    assert lineage["a"] == lineage["b"]
    assert independence["a"] == independence["b"] == "shared_origin"


def test_explicit_independent_review_does_not_union_sources():
    rows = [
        SimpleNamespace(id="a", parent_evidence_id=None, original_source_reference=None),
        SimpleNamespace(id="b", parent_evidence_id=None, original_source_reference=None),
    ]
    relationship = SimpleNamespace(
        source_evidence_id="a",
        related_evidence_id="b",
        source_independence_status="independent",
        origin_group_key=None,
    )
    lineage, independence = build_evidence_lineage(rows, [relationship], ["a", "b"])
    assert lineage == {}
    assert independence == {"a": "independent", "b": "independent"}


def test_non_independent_reviewed_relationship_unifies_lineage_and_overrides_independence():
    rows = [
        SimpleNamespace(id="a", parent_evidence_id=None, original_source_reference=None),
        SimpleNamespace(id="b", parent_evidence_id=None, original_source_reference=None),
    ]
    relationship = SimpleNamespace(
        source_evidence_id="a",
        related_evidence_id="b",
        source_independence_status="shared_origin",
        origin_group_key="forwarded-message-42",
    )
    lineage, independence = build_evidence_lineage(rows, [relationship], ["a", "b"])
    assert lineage["a"] == lineage["b"]
    assert independence == {"a": "shared_origin", "b": "shared_origin"}
