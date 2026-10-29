from types import SimpleNamespace

from app.services.evidence_lineage import build_evidence_lineage, find_exact_content_duplicate_candidates


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

def test_lineage_scope_follows_unselected_ancestor_source_reference_and_stays_person_scoped():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.core.database import Base
    from app.models.care import Caregiver, Evidence, Person
    from app.models.care_continuity import EvidenceSourceRelationship
    from app.services.evidence_lineage import load_evidence_lineage_scope, summarize_evidence_lineage

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add_all([
                Person(id="lineage-person-a", name="Person A"),
                Person(id="lineage-person-b", name="Person B"),
            ])
            db.flush()
            db.add_all([
                Caregiver(id="lineage-caregiver-a", name="Caregiver A", relationship="family",
                          person_id="lineage-person-a"),
                Caregiver(id="lineage-caregiver-b", name="Caregiver B", relationship="family",
                          person_id="lineage-person-b"),
            ])
            db.flush()
            db.add_all([
                Evidence(id="old-source", person_id="lineage-person-a", type="document",
                         source_text="Original care document", uploaded_by_caregiver_id="lineage-caregiver-a",
                         original_source_reference="  clinic / report / 9  "),
                Evidence(id="selected-derivative", person_id="lineage-person-a", type="summary",
                         source_text="Summary copied from source", uploaded_by_caregiver_id="lineage-caregiver-a",
                         parent_evidence_id="old-source"),
                Evidence(id="sibling-copy", person_id="lineage-person-a", type="forwarded_message",
                         source_text="Forwarded report", uploaded_by_caregiver_id="lineage-caregiver-a",
                         original_source_reference="clinic / report / 9"),
                Evidence(id="private-copy", person_id="lineage-person-b", type="document",
                         source_text="Private unrelated record", uploaded_by_caregiver_id="lineage-caregiver-b",
                         original_source_reference="clinic / report / 9"),
            ])
            db.flush()
            db.add(EvidenceSourceRelationship(
                id="lineage-link", person_id="lineage-person-a",
                source_evidence_id="selected-derivative", related_evidence_id="sibling-copy",
                relationship_type="forwarded_from", source_independence_status="shared_origin",
                origin_group_key="clinic-report-9", rationale="Explicit source lineage",
                reviewed_by_caregiver_id="lineage-caregiver-a",
            ))
            db.commit()

            scope = load_evidence_lineage_scope(db, "lineage-person-a", ["selected-derivative"])
            ids = {row.id for row in scope["evidence_rows"]}
            assert {"old-source", "selected-derivative", "sibling-copy"} <= ids
            assert "private-copy" not in ids
            summary = summarize_evidence_lineage(
                scope["evidence_rows"], scope["relationships"], ["selected-derivative", "sibling-copy"]
            )
            shared = [group for group in summary["groups"] if group["source_independence_status"] == "shared_origin"]
            assert shared
            assert any(set(group["evidence_ids"]) == {"selected-derivative", "sibling-copy"} for group in shared)
            assert scope["truncated"] is False
    finally:
        engine.dispose()



def test_exact_content_duplicates_are_candidates_not_proof_of_shared_origin():
    digest = "a" * 64
    rows = [
        SimpleNamespace(id="copy-a", source_text_sha256=digest),
        SimpleNamespace(id="copy-b", source_text_sha256=digest),
        SimpleNamespace(id="different", source_text_sha256="b" * 64),
        SimpleNamespace(id="invalid-hash", source_text_sha256="not-a-hash"),
    ]
    candidates = find_exact_content_duplicate_candidates(
        rows, ["copy-a", "copy-b", "different", "invalid-hash"]
    )
    assert len(candidates) == 1
    assert candidates[0]["evidence_ids"] == ["copy-a", "copy-b"]
    assert candidates[0]["common_origin_established"] is False
    assert candidates[0]["independent_corroboration_established"] is False
    assert candidates[0]["review_required"] is True


def test_explicit_transfer_source_and_reference_establish_shared_lineage():
    rows = [
        SimpleNamespace(
            id="transfer-a", parent_evidence_id=None, original_source_reference=None,
            transfer_source="family-messaging", transfer_reference="message:884",
        ),
        SimpleNamespace(
            id="transfer-b", parent_evidence_id=None, original_source_reference=None,
            transfer_source=" FAMILY-MESSAGING ", transfer_reference=" MESSAGE:884 ",
        ),
        SimpleNamespace(
            id="transfer-c", parent_evidence_id=None, original_source_reference=None,
            transfer_source="family-messaging", transfer_reference="message:885",
        ),
    ]
    lineage, independence = build_evidence_lineage(rows, [], ["transfer-a", "transfer-b", "transfer-c"])
    assert lineage["transfer-a"] == lineage["transfer-b"]
    assert independence["transfer-a"] == independence["transfer-b"] == "shared_origin"
    assert "transfer-c" not in lineage
    assert "transfer-c" not in independence


def test_lineage_scope_discovers_same_person_transfer_family_without_cross_person_leakage():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.core.database import Base
    from app.models.care import Caregiver, Evidence, Person
    from app.services.evidence_lineage import load_evidence_lineage_scope

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add_all([
                Person(id="transfer-person-a", name="Person A"),
                Person(id="transfer-person-b", name="Person B"),
            ])
            db.flush()
            db.add_all([
                Caregiver(id="transfer-caregiver-a", name="Caregiver A", relationship="family", person_id="transfer-person-a"),
                Caregiver(id="transfer-caregiver-b", name="Caregiver B", relationship="family", person_id="transfer-person-b"),
            ])
            db.flush()
            db.add_all([
                Evidence(
                    id="transfer-seed", person_id="transfer-person-a", type="message",
                    source_text="Forwarded account", uploaded_by_caregiver_id="transfer-caregiver-a",
                    transfer_source="family-messaging", transfer_reference="message:884",
                ),
                Evidence(
                    id="transfer-sibling", person_id="transfer-person-a", type="message",
                    source_text="Same forwarded account", uploaded_by_caregiver_id="transfer-caregiver-a",
                    transfer_source=" FAMILY-MESSAGING ", transfer_reference=" MESSAGE:884 ",
                ),
                Evidence(
                    id="transfer-private", person_id="transfer-person-b", type="message",
                    source_text="Other person's private account", uploaded_by_caregiver_id="transfer-caregiver-b",
                    transfer_source="family-messaging", transfer_reference="message:884",
                ),
            ])
            db.flush()
            scope = load_evidence_lineage_scope(db, "transfer-person-a", ["transfer-seed"])
            ids = {row.id for row in scope["evidence_rows"]}
            assert {"transfer-seed", "transfer-sibling"} <= ids
            assert "transfer-private" not in ids
    finally:
        engine.dispose()
