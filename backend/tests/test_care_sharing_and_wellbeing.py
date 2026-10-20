"""Tests for the caregiver trust boundary and caregiver wellbeing.

Both features exist because of a category error in the system that preceded
them. The daily-care form recorded a caregiver's stress level as a CareEvent
belonging to the person being cared for, which meant a daughter's exhaustion was
written into her mother's medical history and, on a shared circle, became visible
to everyone caring for her mother.

The tests below are mostly about refusals, because that is where these features
have to be right. A test that only proves the happy path would pass against an
implementation that shared everything.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.identity_enums import AccountType
from app.core.security import AuthenticatedPrincipal, get_current_principal
from app.models.identity import Account
from app.core.sharing_enums import ShareCategory
from app.main import app
from app.models.care import CareEvent, Caregiver, Evidence, Person
from app.models.care_sharing import CareShareAccessLog
from app.models.wellbeing import WellbeingCheckIn, WellbeingObservation
from app.services.care_sharing import CareSharingService, ShareDenied
from app.services.wellbeing import WellbeingService


# ----------------------------------------------------------------------
# Sharing
# ----------------------------------------------------------------------


@pytest.fixture()
def circle(db):
    """A daughter and a paid carer, which is the arrangement that needs a boundary."""
    person = Person(id="p1", name="Margaret")
    daughter = Caregiver(
        id="c-daughter", name="Jo", email="jo@example.com",
        relationship="daughter", person_id="p1",
    )
    carer = Caregiver(
        id="c-carer", name="Sam", email="sam@example.com",
        relationship="carer", person_id="p1",
    )
    db.add_all([person, daughter, carer])
    db.commit()
    return person, daughter, carer


class TestDefaultIsNo:
    """No grant means no access. The safe direction is the default one."""

    def test_a_caregiver_with_no_grants_sees_nothing(self, db, circle):
        person, _, carer = circle
        assert CareSharingService(db).can_see(person.id, carer.id, "daily_care") is False

    def test_an_unknown_caregiver_id_is_refused_not_defaulted(self, db, circle):
        """A typo in a caregiver id must not read as the owner."""
        person, _, _ = circle
        service = CareSharingService(db)
        assert service.can_see(person.id, "nobody-here", "daily_care") is False

    def test_no_caregiver_at_all_is_refused(self, db, circle):
        person, _, _ = circle
        assert CareSharingService(db).can_see(person.id, None, "daily_care") is False

    def test_an_unknown_category_is_refused(self, db, circle):
        person, _, carer = circle
        service = CareSharingService(db)
        service.grant(person.id, carer.id, "daily_care", relationship="paid_caregiver")
        assert service.can_see(person.id, carer.id, "everything_about_her") is False


class TestCategorySeparation:
    def test_holding_one_category_does_not_confer_another(self, db, circle):
        """The whole reason categories exist."""
        person, daughter, carer = circle
        service = CareSharingService(db)
        service.grant(
            person.id, carer.id, "daily_care", relationship="paid_caregiver",
            granted_by_caregiver_id=daughter.id,
        )
        assert service.can_see(person.id, carer.id, "daily_care") is True
        assert service.can_see(person.id, carer.id, "clinical_documents") is False

    def test_a_wider_grant_does_not_widen_a_narrower_one(self, db, circle):
        """Granting the circle everything must not silently hand over the letters."""
        person, daughter, carer = circle
        service = CareSharingService(db)
        service.grant(
            person.id, carer.id, "daily_care", relationship="paid_caregiver",
            scope="circle", granted_by_caregiver_id=daughter.id,
        )
        assert service.can_see(person.id, carer.id, "medications") is False


class TestRefusedGrants:
    def test_a_non_family_caregiver_cannot_be_given_clinical_documents(self, db, circle):
        person, _, carer = circle
        with pytest.raises(ValueError):
            CareSharingService(db).grant(
                person.id, carer.id, "clinical_documents", relationship="paid_caregiver"
            )

    def test_a_non_family_caregiver_cannot_be_given_meaning(self, db, circle):
        person, _, carer = circle
        with pytest.raises(ValueError):
            CareSharingService(db).grant(
                person.id, carer.id, "meaning_and_what_matters", relationship="friend_or_neighbour"
            )

    def test_clinical_documents_cannot_be_granted_to_the_whole_circle(self, db, circle):
        """A group grant on a medical record includes whoever joins next month."""
        person, daughter, _ = circle
        with pytest.raises(ValueError):
            CareSharingService(db).grant(
                person.id, daughter.id, "clinical_documents",
                relationship="primary_family", scope="circle",
            )

    def test_family_may_hold_clinical_documents_when_named(self, db, circle):
        person, daughter, _ = circle
        row = CareSharingService(db).grant(
            person.id, daughter.id, "clinical_documents", relationship="primary_family"
        )
        assert row.category is ShareCategory.CLINICAL_DOCUMENTS


class TestExpiryAndRevocation:
    def test_an_expired_grant_stops_working_without_being_deleted(self, db, circle):
        """The record of what was granted outlives the grant itself."""
        from datetime import datetime, timedelta, timezone

        person, daughter, carer = circle
        service = CareSharingService(db)
        row = service.grant(
            person.id, carer.id, "medications",
            relationship="paid_caregiver", granted_by_caregiver_id=daughter.id,
        )
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.flush()

        assert service.can_see(person.id, carer.id, "medications") is False
        # Still on file, because "who could see this and until when" needs it.
        assert len(service.grants_for(person.id, carer.id)) == 1

    def test_revocation_takes_effect_immediately(self, db, circle):
        person, daughter, carer = circle
        service = CareSharingService(db)
        row = service.grant(
            person.id, carer.id, "medications",
            relationship="paid_caregiver", granted_by_caregiver_id=daughter.id,
        )
        assert service.can_see(person.id, carer.id, "medications") is True

        service.revoke(row.id, revoked_by_caregiver_id=daughter.id, reason="shift ended")
        assert service.can_see(person.id, carer.id, "medications") is False

    def test_revoking_twice_is_not_an_error(self, db, circle):
        """A family member pressing the button twice must not see a failure."""
        person, daughter, carer = circle
        service = CareSharingService(db)
        row = service.grant(
            person.id, carer.id, "medications",
            relationship="paid_caregiver", granted_by_caregiver_id=daughter.id,
        )
        service.revoke(row.id)
        again = service.revoke(row.id)
        assert again.status.value == "revoked"

    def test_require_raises_for_a_caregiver_without_the_category(self, db, circle):
        person, _, carer = circle
        with pytest.raises(ShareDenied):
            CareSharingService(db).require(person.id, carer.id, "clinical_documents")


class TestAccessLog:
    def test_a_denial_is_recorded(self, db, circle):
        """Denials are the interesting half of an audit trail."""
        person, _, carer = circle
        service = CareSharingService(db)
        service.can_see(person.id, carer.id, "clinical_documents", surface="care_summary")
        db.commit()

        entries = service.access_log(person.id, only_denied=True)
        assert len(entries) == 1
        assert entries[0]["was_allowed"] is False
        assert entries[0]["caregiver_id"] == carer.id

    def test_a_granted_read_is_recorded_too(self, db, circle):
        person, daughter, carer = circle
        service = CareSharingService(db)
        service.grant(
            person.id, carer.id, "daily_care",
            relationship="paid_caregiver", granted_by_caregiver_id=daughter.id,
        )
        service.can_see(person.id, carer.id, "daily_care", surface="pdf_export")
        db.commit()

        rows = db.query(CareShareAccessLog).filter(CareShareAccessLog.was_allowed.is_(True)).all()
        assert len(rows) == 1
        assert rows[0].surface == "pdf_export"


class TestSummaryIsFiltered:
    """The PDF is the surface that leaves the device, so it is the one that matters."""

    def _seed_events(self, db, person_id, caregiver_id):
        from datetime import datetime, timezone

        ev = Evidence(
            id="e1", person_id=person_id, type="note",
            source_text="Woke four times.", uploaded_by_caregiver_id=caregiver_id,
        )
        db.add(ev)
        db.add(
            CareEvent(
                id="ev-1", person_id=person_id, event_type="sleep",
                title="Woke four times", description="Up at night",
                occurred_at=datetime.now(timezone.utc),
                created_by_caregiver_id=caregiver_id, evidence_ids=["e1"],
            )
        )
        db.commit()

    def test_a_caregiver_with_nothing_granted_gets_no_summary_at_all(self, db, circle):
        person, _, carer = circle
        client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: db
        resp = client.get(f"/api/v1/care-summary/{person.id}/json?caregiver_id={carer.id}")
        assert resp.status_code == 403

    def test_a_caregiver_with_daily_care_gets_that_and_not_medications(self, db, circle):
        from app.services.care_summary import CareSummaryBuilder

        person, daughter, carer = circle
        self._seed_events(db, person.id, carer.id)
        service = CareSharingService(db)
        service.grant(
            person.id, carer.id, "daily_care",
            relationship="paid_caregiver", granted_by_caregiver_id=daughter.id,
        )
        db.commit()

        summary = CareSummaryBuilder(db).build(
            person.id, viewer_caregiver_id=carer.id, surface="test"
        )
        assert summary["timeline"], "granted daily care should be present"
        assert summary["medication_adherence"] == [], (
            "medication history leaked to a caregiver who was not granted it"
        )
        assert ShareCategory.MEDICATIONS.value in summary["withheld_categories"]

    def test_a_local_single_user_install_still_gets_everything(self, db, circle):
        """The no-viewer path must keep working, or the app breaks for one person."""
        from app.services.care_summary import CareSummaryBuilder

        person, _, _ = circle
        summary = CareSummaryBuilder(db).build(person.id, viewer_caregiver_id=None)
        assert summary["withheld_categories"] == []


class TestDisclosure:
    def test_a_caregiver_sees_only_their_own_access(self, db, circle):
        person, daughter, carer = circle
        service = CareSharingService(db)
        service.grant(
            person.id, carer.id, "daily_care",
            relationship="paid_caregiver", granted_by_caregiver_id=daughter.id,
        )
        db.commit()

        view = service.disclosure_for(carer.id)
        assert view["people"][0]["person_id"] == person.id
        assert view["people"][0]["categories"] == ["daily_care"]
        # A phrase a caregiver can act on, not an enum value.
        assert "each day" in view["people"][0]["summary"]

    def test_disclosure_describes_letters_in_plain_words(self, db, circle):
        person, daughter, _ = circle
        service = CareSharingService(db)
        service.grant(
            person.id, daughter.id, "clinical_documents", relationship="primary_family"
        )
        db.commit()
        summary = service.disclosure_for(daughter.id)["people"][0]["summary"]
        assert "letters from the doctor" in summary


# ----------------------------------------------------------------------
# Wellbeing
# ----------------------------------------------------------------------


class TestWellbeingStaysOutOfThePersonsRecord:
    """The reason this feature exists.

    Before it, a caregiver's stress level was written as a CareEvent on the
    person being cared for, which put a daughter's exhaustion into her mother's
    medical history and made it visible to the whole circle by default.
    """

    def test_a_check_in_creates_no_evidence_and_no_care_event(self, db, caregiver):
        WellbeingService(db).record_check_in(caregiver.id, "stress", 5)
        db.commit()

        assert db.query(Evidence).filter(Evidence.person_id == "p1").count() == 0
        assert db.query(CareEvent).filter(CareEvent.person_id == "p1").count() == 0

    def test_a_check_in_is_stored_against_the_caregiver(self, db, caregiver):
        WellbeingService(db).record_check_in(caregiver.id, "stress", 5)
        db.commit()
        rows = db.query(WellbeingCheckIn).all()
        assert len(rows) == 1
        assert rows[0].caregiver_id == caregiver.id

    def test_the_caregivers_own_words_are_kept_verbatim(self, db, caregiver):
        WellbeingService(db).record_check_in(
            caregiver.id, "sleep", 2, note="I have not slept properly since Tuesday."
        )
        db.commit()
        assert (
            db.query(WellbeingCheckIn).first().note
            == "I have not slept properly since Tuesday."
        )


class TestWellbeingPoles:
    def test_each_axis_records_its_own_direction(self, db, caregiver):
        """Sleep and stress run opposite ways, and mixing them would invert a trend."""
        WellbeingService(db).record_check_in(caregiver.id, "sleep", 2)
        WellbeingService(db).record_check_in(caregiver.id, "stress", 4)
        db.commit()

        rows = {r.axis.value: r for r in db.query(WellbeingCheckIn).all()}
        assert rows["sleep"].direction.value == "higher_is_better"
        assert rows["stress"].direction.value == "higher_is_worse"

    def test_the_pole_labels_are_kept_with_the_score(self, db, caregiver):
        """A bare number means nothing later without what it was called."""
        WellbeingService(db).record_check_in(caregiver.id, "sleep", 2)
        db.commit()
        row = db.query(WellbeingCheckIn).first()
        assert row.low_label == "Not sleeping"
        assert row.high_label == "Sleeping well"

    def test_a_score_outside_one_to_five_is_refused(self, db, caregiver):
        with pytest.raises(ValueError):
            WellbeingService(db).record_check_in(caregiver.id, "stress", 9)


class TestWellbeingObservations:
    def test_sustained_high_stress_is_reported_as_a_count(self, db, caregiver):
        """A count is checkable. "You are at risk of burnout" is not, and is not claimed."""
        service = WellbeingService(db)
        for score in (5, 5, 4):
            service.record_check_in(caregiver.id, "stress", score)

        observations = service.observe(caregiver.id)
        db.commit()

        assert len(observations) == 1
        statement = observations[0].statement
        assert "3 of your last 3" in statement
        # Nothing clinical. A four-point scale cannot establish burnout.
        for word in ("burnout", "depression", "diagnosis", "risk"):
            assert word not in statement.lower()

    def test_two_high_scores_are_not_a_pattern_yet(self, db, caregiver):
        service = WellbeingService(db)
        for score in (5, 5):
            service.record_check_in(caregiver.id, "stress", score)
        assert service.observe(caregiver.id) == []

    def test_an_observation_records_the_check_ins_it_came_from(self, db, caregiver):
        """So the caregiver can check the arithmetic, or reject it."""
        import json

        service = WellbeingService(db)
        for score in (5, 5, 5):
            service.record_check_in(caregiver.id, "stress", score)
        observation = service.observe(caregiver.id)[0]
        db.commit()

        ids = json.loads(observation.source_check_in_ids)
        assert len(ids) == 3

    def test_a_dismissed_observation_never_comes_back(self, db, caregiver):
        service = WellbeingService(db)
        for score in (5, 5, 5):
            service.record_check_in(caregiver.id, "stress", score)
        observation = service.observe(caregiver.id)[0]
        service.dismiss(observation.id, note="not useful to me")
        db.commit()

        assert service.active_observations(caregiver.id) == []

    def test_no_support_suggestion_is_clinical(self, db, caregiver):
        """Every suggestion has to be something a person can act on tonight."""
        service = WellbeingService(db)
        for score in (5, 5, 5):
            service.record_check_in(caregiver.id, "stress", score)
        service.observe(caregiver.id)
        actions = service.offer_support(caregiver.id)
        db.commit()

        assert actions
        for action in actions:
            lowered = action.description.lower()
            for word in ("diagnos", "prescrib", "condition", "symptom"):
                assert word not in lowered, "clinical suggestion offered: " + action.description

    def test_a_suggestion_is_offered_once_not_on_every_refresh(self, db, caregiver):
        service = WellbeingService(db)
        for score in (5, 5, 5):
            service.record_check_in(caregiver.id, "stress", score)
        service.observe(caregiver.id)
        first = service.offer_support(caregiver.id)
        second = service.offer_support(caregiver.id)
        db.commit()
        assert first
        assert second == []


class TestWellbeingEndpoints:
    @pytest.fixture()
    def client(self, db):
        app.dependency_overrides[get_db] = lambda: db
        return TestClient(app)

    def test_check_in_and_read_back(self, client, db, caregiver):
        resp = client.post(
            "/api/v1/caregiver-wellbeing/check-in",
            json={"caregiver_id": caregiver.id, "axis": "sleep", "score": 2,
                  "note": "Awake most of the night."},
        )
        assert resp.status_code == 200
        assert resp.json()["direction"] == "higher_is_better"

        view = client.get(f"/api/v1/caregiver-wellbeing/{caregiver.id}").json()
        assert view["check_in_count"] == 1
        assert view["axes"]["sleep"]["score"] == 2

    def test_no_route_takes_a_person_id(self, client, caregiver):
        """The scope guarantee is enforced in the API shape, not just in a comment."""
        resp = client.get(f"/api/v1/caregiver-wellbeing/{caregiver.id}")
        assert resp.status_code == 200
        assert "person_id" not in resp.json()

    def test_an_invalid_axis_is_refused(self, client, caregiver):
        resp = client.post(
            "/api/v1/caregiver-wellbeing/check-in",
            json={"caregiver_id": caregiver.id, "axis": "happiness", "score": 3},
        )
        assert resp.status_code == 400


class TestSharingEndpoints:
    @pytest.fixture()
    def client(self, db):
        app.dependency_overrides[get_db] = lambda: db
        account = Account(
            id="care-sharing-test-account",
            email="care-sharing-test@example.com",
            account_type=AccountType.CAREGIVER,
            is_active=True,
        )
        app.dependency_overrides[get_current_principal] = lambda: AuthenticatedPrincipal(
            account, ["p1"]
        )
        return TestClient(app)

    def test_sharing_endpoint_requires_authentication(self, client, circle):
        app.dependency_overrides.pop(get_current_principal, None)
        response = client.get("/api/v1/care-sharing/grants/p1")
        assert response.status_code in (401, 403), response.text

    def test_sharing_endpoint_denies_another_care_context(self, client, circle):
        account = Account(
            id="care-sharing-other-account",
            email="care-sharing-other@example.com",
            account_type=AccountType.CAREGIVER,
            is_active=True,
        )
        app.dependency_overrides[get_current_principal] = lambda: AuthenticatedPrincipal(
            account, ["different-person"]
        )
        response = client.get("/api/v1/care-sharing/grants/p1")
        assert response.status_code == 403, response.text

    def test_a_refused_grant_is_a_400_not_a_silent_success(self, client, circle):
        person, _, carer = circle
        resp = client.post(
            "/api/v1/care-sharing/grant",
            json={"person_id": person.id, "caregiver_id": carer.id,
                  "category": "clinical_documents", "relationship": "paid_caregiver"},
        )
        assert resp.status_code == 400

    def test_onboard_starts_below_what_the_family_would_choose(self, client, circle):
        """Starting permissive would mean nobody reviews it."""
        person, daughter, carer = circle
        resp = client.post(
            "/api/v1/care-sharing/onboard",
            json={"person_id": person.id, "caregiver_id": carer.id,
                  "relationship": "paid_caregiver"},
        )
        assert resp.status_code == 200
        categories = {row["category"] for row in resp.json()}
        assert categories == {"daily_care"}
        assert "clinical_documents" not in categories

    def test_the_access_log_route_reports_denials(self, client, db, circle):
        person, _, carer = circle
        client.get(f"/api/v1/care-sharing/access/{person.id}?caregiver_id={carer.id}&category=daily_care")
        resp = client.get(f"/api/v1/care-sharing/access-log/{person.id}?only_denied=true")
        assert resp.status_code == 200
        assert len(resp.json()["entries"]) == 1
