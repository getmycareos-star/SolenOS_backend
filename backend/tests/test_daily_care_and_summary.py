"""Tests for the daily care structured entry endpoint.

A daily care entry must land in the tables the whole engine reads — not just a
private notes table. Medication, mood, vitals, wellbeing, and general notes
must each produce an evidence record + a CareEvent.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.care import CareEvent, Evidence


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
        assert body["evidence_created"] == 1
        assert body["person_id"] == "p1"

    def test_events_are_linked_to_evidence(self, client, db, person, caregiver):
        """Every CareEvent must reference the Evidence row it was built from."""
        resp = client.post("/api/v1/daily-care/entries", json=_payload())
        assert resp.status_code == 200
        evidence_rows = db.query(Evidence).filter(Evidence.person_id == "p1").all()
        care_events = db.query(CareEvent).filter(CareEvent.person_id == "p1").all()

        assert len(evidence_rows) == 1
        assert len(care_events) >= 3  # mood + vitals + behavior etc.
        for evt in care_events:
            assert evidence_rows[0].id in (evt.evidence_ids or [])

    def test_event_titles_reflect_content(self, client, db, person, caregiver):
        resp = client.post("/api/v1/daily-care/entries", json=_payload())
        assert resp.status_code == 200
        events = db.query(CareEvent).filter(CareEvent.person_id == "p1").all()
        titles = [e.title for e in events]
        assert "Daily mood check" in titles
        assert "Vital signs" in titles
        assert "Behavior observation" in titles

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
        assert resp.status_code == 403

    def test_medication_missed_records_status(self, client, db, person, caregiver):
        payload = _payload()
        payload["medication"] = [
            {"name": "Memantine", "scheduled_time": "09:00", "taken": False, "dose_or_notes": "Skipped — N/V"}
        ]
        resp = client.post("/api/v1/daily-care/entries", json=payload)
        assert resp.status_code == 200
        med_events = db.query(CareEvent).filter(CareEvent.event_type == "medication").all()
        assert len(med_events) == 1
        assert "missed" in med_events[0].title

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
