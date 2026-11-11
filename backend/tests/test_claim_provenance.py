from app.services.claim_provenance import build_claim_provenance


def test_exact_statement_is_linked_only_to_the_source_that_contains_it():
    result = build_claim_provenance(
        "She felt unsteady near the kitchen.",
        [
            {
                "record_type": "care_event",
                "record_id": "event-1",
                "label": "Care entry",
                "source_text": "She felt unsteady near the kitchen.",
                "occurred_at": "2026-11-02T08:00:00+00:00",
            },
            {
                "record_type": "care_event",
                "record_id": "event-2",
                "label": "Care entry",
                "source_text": "She slept well last night.",
            },
        ],
    )
    claim = result["claims"][0]
    assert claim["support_status"] == "matched_saved_wording"
    assert [item["record_id"] for item in claim["source_references"]] == ["event-1"]


def test_unrelated_evidence_is_never_attached_to_a_claim():
    result = build_claim_provenance(
        "Your mother had three incidents of unsteadiness this month.",
        [
            {
                "record_type": "care_event",
                "record_id": "unrelated",
                "source_text": "Your mother attended an appointment this month.",
            }
        ],
    )
    claim = result["claims"][0]
    assert claim["support_status"] == "not_matched_to_saved_wording"
    assert claim["source_references"] == []
    assert result["unmatched_statement_count"] == 1


def test_paraphrase_is_not_misrepresented_as_an_exact_source_match():
    result = build_claim_provenance(
        "She was unstable while walking.",
        [{"record_type": "care_evidence", "record_id": "e1", "source_text": "She felt unsteady while walking."}],
    )
    assert result["claims"][0]["support_status"] == "not_matched_to_saved_wording"


def test_duplicate_sources_are_not_counted_as_independent_corroboration():
    source = {"record_type": "care_evidence", "label": "Saved note", "source_text": "She felt dizzy."}
    result = build_claim_provenance("She felt dizzy.", [
        {**source, "record_id": "e1"},
        {**source, "record_id": "e2"},
    ])
    assert len(result["claims"][0]["source_references"]) == 2
    assert "independent" in result["matching_limit"]
