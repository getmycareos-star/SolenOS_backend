"""Tests for the daily care structured entry endpoint.

A daily capture preserves the full original wording as canonical evidence and
creates typed, queryable records for supported observations. Event/evidence
references must remain resolvable; a successful response alone is not proof.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.care_event import CareEvent
from app.models.evidence import Evidence


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _payload(person_id="p1", caregiver_id="c1", **overrides):
    base = {
        "person_id": person_id,
        "caregiver_id": caregiver_id,
        "mood": "Content",
        "mood_scale": 4,
        "agitation_level": "None",
        "agitation_scale": 1,
        "vitals": {"heart_rate": "72", "oxygen_sat": "98"},
        "behavior_notes": "Engaged in conversation.",
        "sleep_notes": "Slept 6 hours, restless.",
        "eating_notes": "Ate 75% of breakfast.",
        "wellbeing_stress": 2,
        "wellbeing_notes": "Feeling supported today.",
        "general_notes": "Medication taken on time.",
    }
    base.update(overrides)
    return base


class TestDailyCareEntry:
    def test_mood_and_vitals_produce_events(self, client, person, caregiver):
        """A form submission must produce CareEvents the engine reads."""
        resp = client.post("/api/v1/daily-care/entries", json=_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["events_created"] > 0
        assert body["evidence_created"] >= 1
        assert body["person_id"] == "p1"

    def test_events_are_linked_to_evidence(self, client, db, person, caregiver):
        """Every CareEvent must reference an Evidence row that actually exists.

        The endpoint also writes typed daily-capture rows, each with its own
        evidence, so the count is deliberately not asserted: what matters is that
        every event points at evidence that is really on the record. An event
        citing a missing id would pass a count check and still be unauditable.
        """
        resp = client.post("/api/v1/daily-care/entries", json=_payload())
        assert resp.status_code == 200
        evidence_rows = db.query(Evidence).filter(Evidence.person_id == "p1").all()
        care_events = db.query(CareEvent).filter(CareEvent.person_id == "p1").all()

        assert len(evidence_rows) >= 1
        assert len(care_events) >= 3  # mood + vitals + behavior etc.
        known = {row.id for row in evidence_rows}
        for evt in care_events:
            assert evt.evidence_ids, "event with no evidence: " + str(evt.title)
            for cited in evt.evidence_ids:
                assert cited in known, "event cites evidence that does not exist"

    def test_event_titles_and_canonical_evidence_preserve_submitted_content(
        self, client, db, person, caregiver
    ):
        """The original form is one auditable capture; typed observations are separate events."""
        payload = _payload()
        resp = client.post("/api/v1/daily-care/entries", json=payload)
        assert resp.status_code == 200

        events = db.query(CareEvent).filter(CareEvent.person_id == "p1").all()
        titles = [event.title or "" for event in events]
        descriptions = [event.description or "" for event in events]

        # The canonical capture keeps all caregiver wording, including fields
        # that do not have a dedicated typed table (e.g. sleep and wellbeing).
        assert any("Mood: Content" in text for text in titles + descriptions)
        assert any("Behavior: Engaged in conversation." in text for text in titles + descriptions)
        assert any("Sleep: Slept 6 hours, restless." in text for text in titles + descriptions)
        assert any("Wellbeing: stress level 2/5" in text for text in titles + descriptions)
        assert any("General: Medication taken on time." in text for text in titles + descriptions)

        # Numeric/scale values also become separately queryable typed events;
        # their names must describe the actual measurement, not a generic label.
        assert any("heart rate: 72.0 bpm" in title.lower() for title in titles)
        assert any("mood: 4/5 (Bright)" in title for title in titles)
        assert any("agitation: 1/5 (Settled)" in title for title in titles)

        evidence_ids = {
            evidence.id
            for evidence in db.query(Evidence).filter(Evidence.person_id == "p1").all()
        }
        assert evidence_ids
        assert all(event.evidence_ids for event in events)
        assert all(
            evidence_id in evidence_ids
            for event in events
            for evidence_id in (event.evidence_ids or [])
        )

    def test_mood_scale_two_triggers_follow_up(self, client, db, person, caregiver):
        resp = client.post("/api/v1/daily-care/entries", json=_payload(mood_scale=2, mood="Low"))
        body = resp.json()
        assert body["follow_up_candidate"] is True

    def test_stress_level_four_triggers_attention(self, client, db, person, caregiver):
        resp = client.post("/api/v1/daily-care/entries", json=_payload(wellbeing_stress=4))
        body = resp.json()
        assert body["attention_candidate"] is True

    def test_person_not_found(self, client, person, caregiver):
        """A person that does not exist must return 404, not silently create."""
        resp = client.post("/api/v1/daily-care/entries", json=_payload(person_id="ghost"))
        assert resp.status_code == 404

    def test_caregiver_not_authorized(self, client, person, caregiver):
        """A caregiver not tied to this person must be rejected."""
        resp = client.post("/api/v1/daily-care/entries", json=_payload(caregiver_id="c-other"))
        if resp.status_code != 403:
            print(f"Unexpected status code: {resp.status_code}")
            print(f"Response body: {resp.json()}")
            assert resp.status_code == 403

    def test_medication_missed_records_status(self, client, db, person, caregiver):
        payload = _payload()
        payload["medication"] = [
            {"name": "Memantine", "scheduled_time": "09:00", "taken": False, "dose_or_notes": "Skipped — N/V"}
        ]
        resp = client.post("/api/v1/daily-care/entries", json=payload)
        assert resp.status_code == 200
        med_events = db.query(CareEvent).filter(CareEvent.event_type == "medication").all()
        assert len(med_events) >= 1
        # The endpoint writes its own event and the typed service writes another,
        # so the status is checked on the titles rather than on which row it is.
        assert any("missed" in evt.title for evt in med_events)

    def test_list_entries_for_person(self, client, person, caregiver):
        client.post("/api/v1/daily-care/entries", json=_payload())
        resp = client.get(f"/api/v1/daily-care/{person.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 3
        assert all(isinstance(e["id"], str) for e in data)


class TestCareSummary:
    def test_summary_contains_person_and_events(self, client, db, person, caregiver):
        client.post("/api/v1/daily-care/entries", json=_payload())
        resp = client.get(f"/api/v1/summary/{person.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["summary"]["person"]["name"] == "Margaret"
        assert len(body["summary"]["recent_events"]) >= 3

    def test_summary_with_evidence(self, client, db, person, caregiver):
        client.post("/api/v1/daily-care/entries", json=_payload())
        resp = client.get(f"/api/v1/summary/{person.id}?include_evidence=true")
        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert "evidence_trail" in summary
        assert len(summary["evidence_trail"]) >= 1

    def test_summary_without_evidence_omits_trail(self, client, person, caregiver):
        client.post("/api/v1/daily-care/entries", json=_payload())
        resp = client.get(f"/api/v1/summary/{person.id}?include_evidence=false")
        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert summary["evidence_trail"] == []

    def test_summary_has_safety_flags(self, client, person, caregiver):
        client.post("/api/v1/daily-care/entries", json=_payload())
        resp = client.get(f"/api/v1/summary/{person.id}")
        summary = resp.json()["summary"]
        assert "safety_flags" in summary
        assert "attention_candidates" in summary["safety_flags"]
        assert "follow_up_candidates" in summary["safety_flags"]

    def test_summary_person_not_found(self, client, person, caregiver):
        resp = client.get("/api/v1/summary/ghost")
        assert resp.status_code == 404

    def test_summary_has_disclaimer(self, client, person, caregiver):
        client.post("/api/v1/daily-care/entries", json=_payload())
        resp = client.get(f"/api/v1/summary/{person.id}")
        summary = resp.json()["summary"]
        assert "_disclaimer" in summary
        assert "evidence" in summary["_disclaimer"].lower()
