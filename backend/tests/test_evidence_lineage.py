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


def test_lineage_summary_exposes_explicit_origin_and_limits():
    from app.services.evidence_lineage import summarize_evidence_lineage

    evidence = [
        SimpleNamespace(id="a", parent_evidence_id=None, original_source_reference=None),
        SimpleNamespace(id="b", parent_evidence_id=None, original_source_reference=None),
    ]
    link = SimpleNamespace(
        source_evidence_id="b",
        related_evidence_id="a",
        source_independence_status="shared_origin",
        origin_group_key="forwarded-note-9",
    )
    result = summarize_evidence_lineage(evidence, [link], ["a", "b"])
    assert result["status"] == "assessed"
    assert len(result["groups"]) == 1
    assert result["groups"][0]["evidence_ids"] == ["a", "b"]
    assert result["groups"][0]["origin_group_keys"] == ["forwarded-note-9"]
    assert "does not prove" in result["interpretation_limit"]


def test_lineage_summary_keeps_reviewed_independent_sources_separate_from_shared_origin():
    from app.services.evidence_lineage import summarize_evidence_lineage

    evidence = [
        SimpleNamespace(id="independent-a", parent_evidence_id=None, original_source_reference=None),
        SimpleNamespace(id="independent-b", parent_evidence_id=None, original_source_reference=None),
    ]
    link = SimpleNamespace(
        source_evidence_id="independent-a",
        related_evidence_id="independent-b",
        source_independence_status="independent",
        origin_group_key=None,
    )
    result = summarize_evidence_lineage(evidence, [link], ["independent-a", "independent-b"])
    assert len(result["groups"]) == 1
    assert result["groups"][0]["source_independence_status"] == "independent"
    assert result["groups"][0]["basis"] == "explicit_independence_review"
    assert "does not prove" in result["groups"][0]["interpretation_limit"]


def test_selected_siblings_join_through_real_unselected_parent_and_multi_hop_lineage():
    rows = [
        SimpleNamespace(id="root", parent_evidence_id=None, original_source_reference=""),
        SimpleNamespace(id="middle", parent_evidence_id="root", original_source_reference=""),
        SimpleNamespace(id="child-a", parent_evidence_id="middle", original_source_reference=""),
        SimpleNamespace(id="child-b", parent_evidence_id="root", original_source_reference=""),
    ]
    lineage, independence = build_evidence_lineage(
        rows, [], ["child-a", "child-b"]
    )
    assert lineage["child-a"] == lineage["child-b"]
    assert independence == {"child-a": "shared_origin", "child-b": "shared_origin"}


def test_source_reference_on_unselected_ancestor_connects_selected_derivative():
    rows = [
        SimpleNamespace(id="source", parent_evidence_id=None, original_source_reference="record://shared/1"),
        SimpleNamespace(id="derivative", parent_evidence_id="source", original_source_reference=None),
        SimpleNamespace(id="other-copy", parent_evidence_id=None, original_source_reference=" RECORD://SHARED/1 "),
    ]
    lineage, independence = build_evidence_lineage(
        rows, [], ["derivative", "other-copy"]
    )
    assert lineage["derivative"] == lineage["other-copy"]
    assert independence["derivative"] == independence["other-copy"] == "shared_origin"
