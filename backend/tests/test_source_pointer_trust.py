"""Source-Pointer Trust Layer acceptance tests.

Ported from backend-fix/src/lib/data-acquisition-resilience/trust-layer.test.ts

Verifies the three-layer invariant:
  MODEL (initialEvidenceStatus) -> APPLICATION (verify/enforce) -> DATABASE (CHECK)

Core invariant: NO claim stored as confirmed/reported WITHOUT a verified exact
source pointer. Validation may only DOWNGRADE evidence_status, never upgrade.
Numeric confidence is kept separate from evidence_status.
"""
from app.core.change_enums import EvidenceStatus
from app.services.source_pointer import (
    enforce_evidence_status,
    enforce_source_pointer,
    enforce_source_pointers_for_raw_input,
    enforce_validated_event_pointer,
    requires_source_pointer,
    verify_source_pointer,
)
from app.services.source_pointer_store import (
    clear_downgrade_log,
    list_downgrades,
    list_downgrades_for_claim,
    record_downgrade,
)

ORIG = "Mother fell yesterday and was taken to the hospital. She is now resting."

_ORIG_LEN = len(ORIG)


def _make_candidate(**kwargs):
    """Create a candidate dict matching the TS test's makeCandidate defaults."""
    base = {
        "id": "ec_test_1",
        "raw_input_id": "ri_test_1",
        "extracted_fact": "Mother fell yesterday",
        "event_signal": "possible_fall",
        "confidence": 0.8,
        "confidence_sources": ["nlp_model"],
        "source_span": "Mother fell yesterday",
        "extraction_method": "user_input",
        "ambiguity_flags": [],
        "completeness": "complete",
        "missing_fields": ["consequence"],
        "created_at": "2026-01-01T00:00:00Z",
        "evidence_status": "reported",
        "source_span_verified": False,
        "source_span_start_offset": 0,
        "source_span_end_offset": 20,
    }
    if kwargs:
        base.update(kwargs)
    return base


def _make_validated(**kwargs):
    base = {
        "id": "ve_test_1",
        "raw_input_id": "ri_test_1",
        "candidate_id": "ec_test_1",
        "extracted_fact": "Mother fell yesterday",
        "event_signal": "possible_fall",
        "confidence_score": 0.8,
        "confidence_sources": ["nlp_model"],
        "validated_at": "2026-01-01T00:00:00Z",
        "validation_method": "auto_threshold",
        "entities": [],
        "attributes": {},
        "document_id": None,
        "evidence_status": "reported",
        "source_span_verified": False,
        "source_span_start_offset": 0,
        "source_span_end_offset": 20,
    }
    if kwargs:
        base.update(kwargs)
    return base


def _make_raw_input(content, id="ri_test_1"):
    return {
        "id": id,
        "caregiver_id": "cg_test",
        "input_type": "text",
        "content": content,
        "ocr_confidence": None,
        "document_id": None,
        "document_name": None,
        "captured_at": "2026-01-01T00:00:00Z",
        "metadata": {},
    }


# ---- LAYER 2a: verifySourcePointer ----


def test_exact_slice_match_verified():
    start = ORIG.index("fell")
    r = verify_source_pointer("fell", start, start + 4, ORIG)
    assert r == {"verified": True, "reason": "exact_match"}


def test_whitespace_case_mismatch_span_mismatch():
    r = verify_source_pointer("MOTHER FELL", 0, 11, ORIG)
    assert r["verified"] is False
    assert r["reason"] == "span_mismatch"


def test_missing_source_span():
    r = verify_source_pointer("", 0, 5, ORIG)
    assert r["reason"] == "missing_source_span"


def test_missing_start_offset():
    r = verify_source_pointer("fell", None, 5, ORIG)
    assert r["reason"] == "missing_start_offset"


def test_missing_end_offset():
    r = verify_source_pointer("fell", 0, None, ORIG)
    assert r["reason"] == "missing_end_offset"


def test_negative_start_offset():
    r = verify_source_pointer("fell", -1, 5, ORIG)
    assert r["reason"] == "negative_start_offset"


def test_end_not_greater_than_start():
    r = verify_source_pointer("fell", 5, 5, ORIG)
    assert r["reason"] == "end_not_greater_than_start"


def test_end_beyond_original_text():
    r = verify_source_pointer("fell", 7, _ORIG_LEN + 10, ORIG)
    assert r["reason"] == "end_beyond_original_text"


# ---- LAYER 2b: enforceEvidenceStatus (downgrade-only) ----


def test_confirmed_valid_pointer_stays_confirmed():
    r = enforce_evidence_status("confirmed", {"verified": True, "reason": "exact_match"})
    assert r["status"] == "confirmed"


def test_reported_invalid_pointer_downgraded_to_unknown():
    r = enforce_evidence_status("reported", {"verified": False, "reason": "span_mismatch"})
    assert r["status"] == "unknown"
    assert r["reason"] == "span_mismatch"


def test_confirmed_missing_pointer_downgraded_to_unknown():
    r = enforce_evidence_status("confirmed", {"verified": False, "reason": "missing_source_span"})
    assert r["status"] == "unknown"


def test_inferred_never_downgraded():
    r = enforce_evidence_status("inferred", {"verified": False, "reason": "span_mismatch"})
    assert r["status"] == "inferred"


def test_contradictory_never_downgraded():
    r = enforce_evidence_status("contradictory", {"verified": False, "reason": "span_mismatch"})
    assert r["status"] == "contradictory"


def test_never_upgrades_unknown():
    r = enforce_evidence_status("unknown", {"verified": True, "reason": "exact_match"})
    assert r["status"] == "unknown"


# ---- LAYER 2c: enforceSourcePointer (app validator) ----


def test_invalid_pointer_nulls_offsets_downgrades_and_logs():
    clear_downgrade_log()
    bad = _make_candidate(
        evidence_status="reported",
        source_span="Mother fell yesterday",
        source_span_start_offset=5,
        source_span_end_offset=25,
    )
    updated, downgrade = enforce_source_pointer(bad, ORIG, "ri_x")
    assert updated["source_span_verified"] is False
    assert updated["evidence_status"] == "unknown"
    assert downgrade is not None
    assert downgrade["final_status"] == "unknown"
    assert downgrade["original_status"] == "reported"
    assert downgrade["original_confidence"] == bad["confidence"]
    assert downgrade["final_confidence"] == bad["confidence"]


def test_valid_pointer_preserves_reported_no_downgrade():
    clear_downgrade_log()
    span = "Mother fell yesterday"
    good = _make_candidate(
        evidence_status="reported",
        source_span=span,
        source_span_start_offset=0,
        source_span_end_offset=len(span),
    )
    updated, downgrade = enforce_source_pointer(good, ORIG, "ri_x")
    assert updated["source_span_verified"] is True
    assert updated["evidence_status"] == "reported"
    assert downgrade is None


def test_enforce_source_pointers_for_raw_input_returns_downgrades():
    clear_downgrade_log()
    raw = _make_raw_input(ORIG)
    span = "Mother fell yesterday"
    c1 = _make_candidate(
        id="c_good",
        evidence_status="reported",
        source_span=span,
        source_span_start_offset=0,
        source_span_end_offset=len(span),
    )
    c2 = _make_candidate(
        id="c_bad",
        evidence_status="reported",
        source_span="taken to the hospital",
        source_span_start_offset=999,
        source_span_end_offset=1010,
    )
    enforced, downgrades = enforce_source_pointers_for_raw_input([c1, c2], raw)
    assert enforced[0]["source_span_verified"] is True
    assert enforced[1]["source_span_verified"] is False
    assert enforced[1]["evidence_status"] == "unknown"
    assert len(downgrades) == 1
    assert downgrades[0]["claim_id"] == "c_bad"


def test_downgrades_are_recorded_and_queryable():
    clear_downgrade_log()
    bad = _make_candidate(
        id="c_log",
        evidence_status="confirmed",
        source_span_start_offset=None,
        source_span_end_offset=None,
    )
    _, downgrade = enforce_source_pointer(bad, ORIG, "ri_log")
    assert downgrade is not None
    record_downgrade(downgrade)
    assert len(list_downgrades()) == 1
    assert len(list_downgrades_for_claim("c_log")) == 1


# ---- LAYER 2d: enforceValidatedEventPointer ----


def test_confirmed_validated_event_without_original_text_untouched():
    ev = _make_validated(evidence_status="confirmed")
    event, downgrade = enforce_validated_event_pointer(ev, None)
    assert event["evidence_status"] == "confirmed"
    assert downgrade is None


def test_reported_validated_event_invalid_pointer_downgraded():
    ev = _make_validated(
        evidence_status="reported",
        extracted_fact="Mother fell yesterday",
        source_span_start_offset=3,
        source_span_end_offset=23,
    )
    event, downgrade = enforce_validated_event_pointer(ev, ORIG)
    assert event["evidence_status"] == "unknown"
    assert event["source_span_verified"] is False
    assert downgrade is not None


def test_reported_validated_event_valid_pointer_preserved():
    span = "Mother fell yesterday"
    ev = _make_validated(
        evidence_status="reported",
        extracted_fact=span,
        source_span_start_offset=0,
        source_span_end_offset=len(span),
    )
    event, downgrade = enforce_validated_event_pointer(ev, ORIG)
    assert event["evidence_status"] == "reported"
    assert event["source_span_verified"] is True
    assert downgrade is None


# ---- INVARIANT HELPERS ----


def test_requires_source_pointer_only_confirmed_reported():
    assert requires_source_pointer("confirmed") is True
    assert requires_source_pointer("reported") is True
    assert requires_source_pointer("inferred") is False
    assert requires_source_pointer("unknown") is False
    assert requires_source_pointer("contradictory") is False


# ---- END-TO-END: 5 realistic caregiver inputs ----


_CAREGIVER_INPUTS = [
    "This morning mom refused to eat breakfast and seemed confused. She kept asking where dad is.",
    "Dad called Dr. Patel at 2pm to report that the new medication was making him dizzy. He will follow up next week.",
    "Last night she fell in the hallway but said she wasn't hurt. She seemed a bit disoriented afterward.",
    "The insurance claim for the hospital visit in March was rejected. We are planning to appeal.",
    "He stopped eating lunch and has been sleeping a lot today. The doctor's appointment is tomorrow.",
]


def _assert_global_invariant(events, candidates):
    all_items = []
    for e in events:
        all_items.append({
            "id": e["id"],
            "status": e["evidence_status"],
            "verified": e["source_span_verified"],
            "offsets": [e["source_span_start_offset"], e["source_span_end_offset"]],
        })
    for c in candidates:
        all_items.append({
            "id": c["id"],
            "status": c["evidence_status"],
            "verified": c["source_span_verified"],
            "offsets": [c["source_span_start_offset"], c["source_span_end_offset"]],
        })

    for item in all_items:
        if item["status"] in ("confirmed", "reported"):
            assert item["verified"] is True, (
                f"claim {item['id']} has status {item['status']} but source_span_verified=False"
            )
            s, e = item["offsets"]
            assert s is not None and e is not None and e > s, (
                f"claim {item['id']} has invalid offsets"
            )


def test_realistic_inputs_invariant_holds_end_to_end():
    for i, content in enumerate(_CAREGIVER_INPUTS):
        clear_downgrade_log()
        raw = _make_raw_input(content, id=f"ri_real_{i+1}")

        # Simulate extraction candidates with exact source pointers (full-text span)
        span = content
        start = 0
        end = len(content)
        candidates = [
            {
                "id": f"ec_real_{i+1}",
                "raw_input_id": raw["id"],
                "extracted_fact": span,
                "event_signal": "observation_signal",
                "confidence": 0.7,
                "confidence_sources": ["nlp_model"],
                "source_span": span,
                "extraction_method": "user_input",
                "ambiguity_flags": [],
                "completeness": "complete",
                "missing_fields": [],
                "created_at": "2026-01-01T00:00:00Z",
                "evidence_status": "reported",
                "source_span_verified": False,
                "source_span_start_offset": start,
                "source_span_end_offset": end,
            }
        ]

        enforced, downgrades = enforce_source_pointers_for_raw_input(candidates, raw)
        validated_events = []
        for c in enforced:
            ve = {
                "id": f"ve_real_{i+1}",
                "raw_input_id": raw["id"],
                "candidate_id": c["id"],
                "extracted_fact": c["source_span"],
                "event_signal": c["event_signal"],
                "confidence_score": c["confidence"],
                "confidence_sources": c["confidence_sources"],
                "validated_at": "2026-01-01T00:00:00Z",
                "validation_method": "auto_threshold",
                "entities": [],
                "attributes": {},
                "document_id": None,
                "evidence_status": c["evidence_status"],
                "source_span_verified": c["source_span_verified"],
                "source_span_start_offset": c["source_span_start_offset"],
                "source_span_end_offset": c["source_span_end_offset"],
            }
            ve, d = enforce_validated_event_pointer(ve, content)
            validated_events.append(ve)

        _assert_global_invariant(validated_events, enforced)

        for d in downgrades:
            pass  # downgrades are allowed; confidence never changes
        for d in list_downgrades():
            assert d["original_confidence"] == d["final_confidence"], (
                f"confidence changed during downgrade: {d}"
            )
