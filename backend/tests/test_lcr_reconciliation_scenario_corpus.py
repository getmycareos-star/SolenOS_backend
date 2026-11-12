"""Independent expected-state corpus for longitudinal LCR reconciliation.

The expected outcomes are authored in this fixture layer, not derived from Delta Engine
responses. This file qualifies corpus completeness only; execution against the engine is
a separate benchmark gate.
"""
from collections import Counter

import pytest


FAMILY_COUNTS = {
    "temporal_ordering": 15,
    "duplicate_observations": 15,
    "corrections_supersession": 15,
    "contradictory_evidence": 15,
    "stale_documents": 10,
    "missing_information": 10,
    "baseline_pattern_changes": 10,
    "medication_safety": 10,
}

# Distinct longitudinal domains and source-grounded values prevent the 100-case
# corpus from being 100 copies of one sleep/medication story.
OBSERVATION_VARIANTS = [
    ("sleep", "daughter reports poor sleep", "daughter reports sleep improved", "sleep", "sleep improved"),
    ("mobility", "daughter reports unsteady standing", "daughter reports steadier standing", "standing", "steadier standing"),
    ("appetite", "daughter reports reduced appetite", "daughter reports appetite improved", "appetite", "appetite improved"),
    ("pain", "daughter reports knee pain", "daughter reports knee pain eased", "knee pain", "knee pain eased"),
    ("orientation", "daughter reports disorientation", "daughter reports orientation improved", "orientation", "orientation improved"),
    ("hydration", "daughter reports drinking less than usual", "daughter reports fluid intake improved", "fluid intake", "fluid intake improved"),
    ("agitation", "daughter reports increased agitation", "daughter reports agitation reduced", "agitation", "agitation reduced"),
    ("toileting", "daughter reports nighttime toileting increased", "daughter reports nighttime toileting eased", "toileting", "toileting eased"),
    ("communication", "daughter reports fewer clear responses", "daughter reports clearer responses", "responses", "clearer responses"),
    ("participation", "daughter reports less participation in meals", "daughter reports better participation in meals", "participation", "better participation"),
]


def _make_scenario(case_number: int, family: str, variant: int) -> dict:
    case_id = f"lcr-{case_number:03d}"
    person_id = f"person-{case_number:03d}"
    evidence = [f"evidence-{case_number:03d}-{n:02d}" for n in range(1, 10)]
    observation_topic, initial_claim, later_claim, assertion_name, later_value = OBSERVATION_VARIANTS[variant % len(OBSERVATION_VARIANTS)]
    initial_value = initial_claim.removeprefix("daughter reports ").strip()
    events = [
        {"id": f"{case_id}-01", "day": 1, "arrival": 1, "kind": "observation",
         "topic": observation_topic, "claim": initial_claim, "assertion_name": assertion_name,
         "assertion_value": initial_value, "reporter": "daughter",
         "evidence_id": evidence[0], "classification": "new_observation"},
        {"id": f"{case_id}-02", "day": 3, "arrival": 2, "kind": "document",
         "topic": "medication", "claim": "hospital document lists medication A",
         "reporter": "hospital_document", "evidence_id": evidence[1],
         "classification": "historical_document_mention"},
        {"id": f"{case_id}-03", "day": 5, "arrival": 3, "kind": "observation",
         "topic": "medication_administration", "claim": "son reports a missed dose",
         "reporter": "son", "evidence_id": evidence[2],
         "classification": "reported_administration_event"},
        {"id": f"{case_id}-04", "day": 7, "arrival": 4, "kind": "caregiver_report",
         "topic": "medication", "claim": "daughter reports medication changed earlier",
         "reporter": "daughter", "evidence_id": evidence[3],
         "classification": "unverified_change_report"},
        {"id": f"{case_id}-05", "day": 9, "arrival": 5, "kind": "document",
         "topic": "medication", "claim": "older document lists medication A",
         "reporter": "older_document", "evidence_id": evidence[4],
         "classification": "historical_conflicting_evidence"},
        {"id": f"{case_id}-06", "day": 12, "arrival": 6, "kind": "correction",
         "topic": "sleep", "claim": "daughter corrects original observation date",
         "reporter": "daughter", "evidence_id": evidence[5],
         "corrects_event_id": f"{case_id}-01", "classification": "correction_supersedes_date"},
        {"id": f"{case_id}-07", "day": 14, "arrival": 7, "kind": "observation",
         "topic": observation_topic, "claim": later_claim, "assertion_name": assertion_name,
         "assertion_value": later_value, "reporter": "daughter",
         "evidence_id": evidence[6], "classification": "new_observation"},
        {"id": f"{case_id}-08", "day": 18, "arrival": 8, "kind": "observation",
         "topic": "cognition", "claim": "another caregiver reports increased confusion",
         "assertion_name": "confusion", "assertion_value": "increased confusion",
         "reporter": "son", "evidence_id": evidence[7],
         "classification": "new_observation"},
    ]

    # Each family changes a material property while retaining the same independently
    # specified conflict, correction, and missing-information requirements.
    if family == "temporal_ordering":
        events[6]["arrival"] = 10 + (variant % 3)
        events[7]["arrival"] = 2 + (variant % 4)
        events[0]["arrival"] = 9
    elif family == "duplicate_observations":
        duplicate = dict(events[0])
        duplicate.update({
            "id": f"{case_id}-09", "arrival": 9, "duplicate_of": events[0]["id"],
            "evidence_id": evidence[8], "classification": "duplicate_same_occurrence",
        })
        events.append(duplicate)
    elif family == "corrections_supersession":
        events[5]["corrected_day"] = 1 + (variant % 4)
        events[5]["claim"] = "caregiver corrects original observation date and retains prior value"
    elif family == "contradictory_evidence":
        events[3]["claim"] = f"caregiver reports medication B variant {variant}"
        events[4]["claim"] = "older signed document lists medication A"
    elif family == "stale_documents":
        events[1]["effective_day_offset"] = -(365 + variant)
        events[4]["effective_day_offset"] = -(730 + variant)
    elif family == "missing_information":
        events[2]["claim"] = "missed dose reported; administration record unavailable"
        events[2]["missing_information"] = ["whether dose was later administered"]
    elif family == "baseline_pattern_changes":
        events[0]["baseline"] = "usual sleep was stable in prior documented period"
        events[6]["claim"] = "sleep improved relative to the documented baseline"
    elif family == "medication_safety":
        events[1]["claim"] = "document mentions medication A; no prescribing instruction supplied"
        events[3]["claim"] = "caregiver believes medication may have changed; prescriber confirmation absent"
        events[4]["claim"] = "older document lists medication A; current prescription is not established"
    else:
        raise AssertionError(f"Unknown scenario family: {family}")

    event_ids = [event["id"] for event in events]
    evidence_refs = [
        {"event_id": event["id"], "evidence_id": event["evidence_id"]}
        for event in events
    ]
    return {
        "scenario_id": case_id,
        "family": family,
        "person_id": person_id,
        "initial_lcr": {
            "assertions": [
                {"topic": assertion_name, "value": initial_value, "status": "reported"},
                {"topic": "medication", "value": "current_prescription_unknown", "status": "unknown"},
            ],
            "historical_record_ids": [],
        },
        "incoming_events": events,
        "expected_final_state": {
            "assertions": [
                {"topic": assertion_name, "value": later_value, "status": "reported",
                 "source_event_id": f"{case_id}-07"},
                {"topic": "confusion", "value": "increased confusion", "status": "reported",
                 "source_event_id": f"{case_id}-08"},
            ],
            "medication_current_state": "unresolved_not_promoted",
            "historical_record_ids": event_ids,
            "corrected_record_ids": [f"{case_id}-01"],
            "superseded_record_ids": [],
            "duplicate_groups": (
                [[f"{case_id}-01", f"{case_id}-09"]]
                if family == "duplicate_observations" else []
            ),
            "evidence_references": evidence_refs,
            "unresolved_questions": [
                "What is the currently prescribed medication and dose?",
                "Was the reported missed dose later administered?",
                "What is the corrected date of the original sleep observation?",
            ],
            "missing_information": [
                "No complete medication administration record was supplied.",
                "No current prescriber-confirmed medication list was supplied.",
            ],
        },
        "required_decisions": {
            "conflict_event_ids": [f"{case_id}-04", f"{case_id}-05"],
            "correction_event_ids": [f"{case_id}-06"],
            "missing_information_required": True,
            "absence_is_not_negative_evidence": True,
        },
    }


def build_curated_scenarios() -> list[dict]:
    scenarios = []
    number = 0
    for family, count in FAMILY_COUNTS.items():
        for variant in range(count):
            number += 1
            scenarios.append(_make_scenario(number, family, variant))
    assert number == 100
    return scenarios


SCENARIOS = build_curated_scenarios()


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item["scenario_id"])
def test_curated_longitudinal_scenario_has_independent_expected_state(scenario):
    events = scenario["incoming_events"]
    expected = scenario["expected_final_state"]
    decisions = scenario["required_decisions"]

    assert 5 <= len(events) <= 15
    assert len({event["id"] for event in events}) == len(events)
    assert all(event["evidence_id"] for event in events)
    assert decisions["conflict_event_ids"]
    assert decisions["correction_event_ids"]
    assert decisions["missing_information_required"] is True
    assert decisions["absence_is_not_negative_evidence"] is True
    assert expected["medication_current_state"] == "unresolved_not_promoted"
    assert expected["historical_record_ids"] == [event["id"] for event in events]
    assert {ref["event_id"] for ref in expected["evidence_references"]} == {
        event["id"] for event in events
    }
    assert expected["unresolved_questions"]
    assert scenario["initial_lcr"]["assertions"]


def test_curated_corpus_has_exact_family_distribution_and_100_unique_people():
    assert len(SCENARIOS) == 100
    assert len({scenario["scenario_id"] for scenario in SCENARIOS}) == 100
    assert len({scenario["person_id"] for scenario in SCENARIOS}) == 100
    assert Counter(scenario["family"] for scenario in SCENARIOS) == Counter(FAMILY_COUNTS)
