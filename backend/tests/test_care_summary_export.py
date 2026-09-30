"""Tests for the Living Care Summary export.

The properties worth protecting are the ones a clinician depends on: every row
is attributed, refusals stay distinct from misses, and what the record does not
establish is stated rather than quietly left out.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.services.care_summary import DISCLAIMER, CareSummaryBuilder


@pytest.fixture()
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _now():
    return datetime.now(timezone.utc)


def _record_med(db, person, caregiver, name, status, days_ago=0):
    from app.services.daily_capture import DailyCaptureService

    record = DailyCaptureService(db).record_medication(
        {
            "person_id": person.id,
            "caregiver_id": caregiver.id,
            "medication_name": name,
            "status": status,
            "administered_at": _now() - timedelta(days=days_ago),
        }
    )
    db.commit()
    return record


class TestSummaryStructure:
    def test_the_summary_carries_a_non_device_disclaimer(self, db, person, caregiver):
        """The one line that has to survive onto the printed page."""
        summary = CareSummaryBuilder(db).build(person.id)
        assert "not a medical device" in DISCLAIMER.lower()
        assert "no diagnosis" in DISCLAIMER.lower()
        assert summary["disclaimer"] == DISCLAIMER

    def test_a_person_with_nothing_recorded_still_produces_a_summary(
        self, db, person, caregiver
    ):
        """An empty record must still export, and must say what is missing.

        A summary that 404s or renders blank leaves the caregiver with nothing
        to hand over, which is the exact moment they needed it.
        """
        summary = CareSummaryBuilder(db).build(person.id)
        assert summary["person"]["id"] == person.id
        assert summary["timeline"] == []
        gaps = {g["gap"] for g in summary["information_gaps"]}
        assert any("medication" in g.lower() for g in gaps)
        assert any("vital" in g.lower() for g in gaps)

    def test_an_unknown_person_raises(self, db, caregiver):
        with pytest.raises(ValueError):
            CareSummaryBuilder(db).build("nobody")

    def test_every_timeline_row_is_attributed(self, db, person, caregiver):
        """A clinician's first question is usually "who saw this?"."""
        _record_med(db, person, caregiver, "Donepezil", "given")
        summary = CareSummaryBuilder(db).build(person.id)
        assert summary["timeline"]
        for event in summary["timeline"]:
            assert event["recorded_by"] == caregiver.name
            assert event["recorded_by"] != "unattributed"


class TestMedicationAdherence:
    def test_a_refusal_is_reported_separately_from_a_miss(self, db, person, caregiver):
        _record_med(db, person, caregiver, "Donepezil", "refused")
        _record_med(db, person, caregiver, "Donepezil", "missed")
        _record_med(db, person, caregiver, "Donepezil", "given")

        rows = {r["medication"]: r for r in CareSummaryBuilder(db).build(person.id)["medication_adherence"]}
        item = rows["Donepezil"]
        assert item["refused"] == 1
        assert item["missed"] == 1
        assert item["given"] == 1

    def test_a_decline_is_raised_for_discussion(self, db, person, caregiver):
        for offset, status in [(13, "given"), (12, "given"), (10, "given"), (1, "missed"), (0, "missed")]:
            _record_med(db, person, caregiver, "Donepezil", status, days_ago=offset)

        summary = CareSummaryBuilder(db).build(person.id)
        item = {r["medication"]: r for r in summary["medication_adherence"]}["Donepezil"]
        assert item["declining"] is True
        assert any("Donepezil" in p for p in summary["discussion_points"])

    def test_a_refusal_raises_a_question_even_without_a_decline(self, db, person, caregiver):
        _record_med(db, person, caregiver, "Ropinirole", "refused")
        summary = CareSummaryBuilder(db).build(person.id)
        assert any("Ropinirole" in p and "refused" in p for p in summary["discussion_points"])

    def test_the_summary_says_why_is_not_established(self, db, person, caregiver):
        _record_med(db, person, caregiver, "Donepezil", "missed")
        summary = CareSummaryBuilder(db).build(person.id)
        item = summary["medication_adherence"][0]
        assert item["what_is_not_established"]

    def test_declines_are_listed_first(self, db, person, caregiver):
        _record_med(db, person, caregiver, "Aaa Stable", "given")
        for offset, status in [(13, "given"), (12, "given"), (10, "given"), (1, "missed"), (0, "missed")]:
            _record_med(db, person, caregiver, "Bbb Declining", status, days_ago=offset)
        summary = CareSummaryBuilder(db).build(person.id)
        assert summary["medication_adherence"][0]["medication"] == "Bbb Declining"


class TestVitalsAndScales:
    def test_only_out_of_range_vitals_are_listed(self, db, person, caregiver):
        from app.services.daily_capture import DailyCaptureService

        service = DailyCaptureService(db)
        service.record_vital({
            "person_id": person.id, "caregiver_id": caregiver.id,
            "vital_type": "heart_rate", "value": 72, "measured_at": _now(),
        })
        service.record_vital({
            "person_id": person.id, "caregiver_id": caregiver.id,
            "vital_type": "systolic_bp", "value": 195, "measured_at": _now(),
        })
        db.commit()

        flagged = CareSummaryBuilder(db).build(person.id)["vitals_out_of_range"]
        assert len(flagged) == 1
        assert flagged[0]["vital"] == "systolic_bp"
        # The flag must not become an explanation.
        assert "not a finding" in flagged[0]["note"].lower()

    def test_scales_carry_their_pole_labels(self, db, person, caregiver):
        from app.services.daily_capture import DailyCaptureService

        DailyCaptureService(db).record_scale({
            "person_id": person.id, "caregiver_id": caregiver.id,
            "axis": "agitation", "score": 4, "observed_at": _now(),
        })
        db.commit()

        scales = CareSummaryBuilder(db).build(person.id)["structured_observations"]
        assert scales[0]["axis"] == "agitation"
        assert scales[0]["score"] == 4
        # A bare 4 is meaningless on a printed page.
        assert "Very agitated" in scales[0]["scale"]


class TestExportEndpoints:
    def test_the_json_export_serves_the_structured_record(self, db, person, caregiver, client):
        _record_med(db, person, caregiver, "Donepezil", "given")
        response = client.get("/api/v1/care-summary/{}/json".format(person.id))
        assert response.status_code == 200
        body = response.json()
        assert body["person"]["id"] == person.id
        assert "medication_adherence" in body
        assert body["disclaimer"]

    def test_the_pdf_export_is_a_real_attachment(self, db, person, caregiver, client):
        _record_med(db, person, caregiver, "Donepezil", "given")
        response = client.get("/api/v1/care-summary/{}/pdf".format(person.id))
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "attachment" in response.headers["content-disposition"]
        assert ".pdf" in response.headers["content-disposition"]
        assert response.content[:5] == b"%PDF-"

    def test_the_pdf_renders_with_no_record_at_all(self, db, person, caregiver, client):
        """The empty case must still produce a document, not a crash."""
        response = client.get("/api/v1/care-summary/{}/pdf".format(person.id))
        assert response.status_code == 200
        assert response.content[:5] == b"%PDF-"

    def test_an_unknown_person_returns_404_for_both_formats(
        self, db, person, caregiver, client
    ):
        assert client.get("/api/v1/care-summary/nobody/json").status_code == 404
        assert client.get("/api/v1/care-summary/nobody/pdf").status_code == 404

    def test_the_pdf_states_it_is_not_a_medical_device(self, db, person, caregiver, client):
        from pypdf import PdfReader
        from io import BytesIO

        response = client.get("/api/v1/care-summary/{}/pdf".format(person.id))
        text = PdfReader(BytesIO(response.content)).pages[0].extract_text()
        # PDF text wraps mid-sentence, so compare on whitespace-collapsed text
        # rather than on the raw extraction.
        flattened = " ".join(text.lower().split())
        assert "not a medical device" in flattened
        assert "makes no diagnosis" in flattened

    def test_the_pdf_lists_the_person_and_the_window(self, db, person, caregiver, client):
        from pypdf import PdfReader
        from io import BytesIO

        response = client.get("/api/v1/care-summary/{}/pdf".format(person.id))
        text = PdfReader(BytesIO(response.content)).pages[0].extract_text()
        flattened = " ".join(text.split())
        assert person.name in flattened
        assert "30 days of record" in flattened
