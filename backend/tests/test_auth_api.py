from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.core.authz import require_person_access
from app.models.auth_identity import AccountPersonAccess, AuthAccount, AuthSession, CaregiverAccountLink, CaregiverInvitation, CaregiverProfileAccountLink, CaregiverProfileInvitation
from app.models.care import Person, Caregiver as CaregiverModel, Evidence as EvidenceModel, Insight as InsightModel
from app.models.change import Baseline
from app.models.causal import CausalHypothesis
from app.models.caregiver_intelligence import CaregiverProfile as CaregiverProfileModel, CaregiverObservation as CaregiverObservationModel
from app.models.safety import SafetyAuditRecordModel, SafetyEventModel
from app.models.action import Action as ActionModel
from app.models.uncertainty import InformationGap





def test_cors_allows_deployed_netlify_frontend():
    cors = next(middleware for middleware in app.user_middleware if middleware.cls.__name__ == "CORSMiddleware")
    assert "https://solenosai.netlify.app" in cors.kwargs["allow_origins"]


def test_signup_login_current_account_and_logout(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine, tables=[Person.__table__, CaregiverModel.__table__, EvidenceModel.__table__, AuthAccount.__table__, AuthSession.__table__, AccountPersonAccess.__table__, CaregiverAccountLink.__table__, CaregiverInvitation.__table__, InformationGap.__table__, Baseline.__table__, CausalHypothesis.__table__, CaregiverProfileModel.__table__, CaregiverObservationModel.__table__, CaregiverProfileAccountLink.__table__, CaregiverProfileInvitation.__table__, SafetyAuditRecordModel.__table__, SafetyEventModel.__table__, ActionModel.__table__, InsightModel.__table__])
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.api.v1._log_and_learn", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.api.caregiver_intelligence._log_and_learn", lambda *args, **kwargs: None)
    try:
        with TestClient(app) as client:
            signup = client.post("/api/v1/auth/signup", json={
                "email": "caregiver@example.com",
                "password": "a-long-password-2026",
                "display_name": "Caregiver",
            })
            assert signup.status_code == 201, signup.text
            assert signup.json()["email"] == "caregiver@example.com"
            assert "password_hash" not in signup.json()

            duplicate = client.post("/api/v1/auth/signup", json={
                "email": "CAREGIVER@example.com",
                "password": "a-long-password-2026",
            })
            assert duplicate.status_code == 409

            bad_login = client.post("/api/v1/auth/login", json={
                "email": "caregiver@example.com",
                "password": "incorrect-password-2026",
            })
            assert bad_login.status_code == 401

            login = client.post("/api/v1/auth/login", json={
                "email": "caregiver@example.com",
                "password": "a-long-password-2026",
            })
            assert login.status_code == 200, login.text
            token = login.json()["access_token"]
            assert token
            with TestingSession() as db:
                stored = db.query(AuthSession).filter(AuthSession.account_id == signup.json()["id"]).one()
                assert stored.token_digest != token
                assert len(stored.token_digest) == 64

            no_token = client.get("/api/v1/auth/me")
            assert no_token.status_code == 401
            unauthenticated_care_read = client.get("/api/v1/uncertainty/gaps/not-authorized")
            assert unauthenticated_care_read.status_code == 401
            unauthenticated_change_read = client.get("/api/v1/change/baselines/not-authorized")
            assert unauthenticated_change_read.status_code == 401
            unauthenticated_causal_read = client.get("/api/v1/causal/hypotheses/not-authorized")
            assert unauthenticated_causal_read.status_code == 401
            unauthenticated_legacy_read = client.get("/api/v1/persons/not-authorized")
            assert unauthenticated_legacy_read.status_code == 401
            unauthenticated_caregiver_read = client.get("/api/v1/caregiver-profiles/not-authorized")
            assert unauthenticated_caregiver_read.status_code == 401
            unauthenticated_safety_read = client.get("/api/v1/safety/audit/not-authorized")
            assert unauthenticated_safety_read.status_code == 401
            unauthenticated_action_read = client.get("/api/v1/actions/actions/not-authorized")
            assert unauthenticated_action_read.status_code == 401
            unauthenticated_action_safety = client.post("/api/v1/actions/actions/safety-check", json={"source_evidence_id": "not-authorized", "source_passage": "test", "original_text": "test", "extraction_confidence": 0.5, "normalized_action": "test"})
            assert unauthenticated_action_safety.status_code == 401
            unauthenticated_situation_write = client.post("/api/situation", json={"person_id": "not-authorized", "caregiver_id": "not-authorized", "raw_input": "test"})
            assert unauthenticated_situation_write.status_code == 401

            headers = {"Authorization": f"Bearer {token}"}
            current = client.get("/api/v1/auth/me", headers=headers)
            assert current.status_code == 200, current.text
            assert current.json()["email"] == "caregiver@example.com"
            legacy_created = client.post("/api/v1/persons", headers=headers, json={"name": "Legacy Care Recipient"})
            assert legacy_created.status_code == 200, legacy_created.text
            assert client.get("/api/v1/persons/" + legacy_created.json()["id"], headers=headers).status_code == 200

            created_context = client.post(
                "/api/v1/auth/care-contexts",
                headers=headers,
                json={"name": "Care Recipient"},
            )
            assert created_context.status_code == 201, created_context.text
            context = created_context.json()
            assert context["name"] == "Care Recipient"
            assert context["role"] == "owner"
            assert set(context["permissions"]) == {"read", "write", "admin"}
            listed = client.get("/api/v1/auth/care-contexts", headers=headers)
            assert listed.status_code == 200
            assert {item["person_id"] for item in listed.json()} == {context["person_id"], legacy_created.json()["id"]}
            own_safety_audits = client.get("/api/v1/safety/audit/" + context["person_id"], headers=headers)
            assert own_safety_audits.status_code == 200, own_safety_audits.text
            assert own_safety_audits.json() == []
            own_actions = client.get("/api/v1/actions/actions/" + context["person_id"], headers=headers)
            assert own_actions.status_code == 200, own_actions.text
            assert own_actions.json() == []
            own_uncompleted_actions = client.get("/api/v1/actions/actions/" + context["person_id"] + "/uncompleted", headers=headers)
            assert own_uncompleted_actions.status_code == 200, own_uncompleted_actions.text
            assert own_uncompleted_actions.json() == []
            with TestingSession() as db:
                caregiver = CaregiverModel(name="Authorized caregiver", email="owner-caregiver@example.com", relationship="family", person_id=context["person_id"], timezone="UTC")
                db.add(caregiver)
                db.flush()
                caregiver_profile = CaregiverProfileModel(person_id=context["person_id"], name="Authorized caregiver profile", email="owner-profile@example.com", relationship="family")
                db.add(caregiver_profile)
                db.flush()
                own_evidence = EvidenceModel(person_id=context["person_id"], type="note", source_text="Call the clinic tomorrow.", uploaded_by_caregiver_id=caregiver.id, confidence=1.0)
                foreign_person = Person(name="Private unrelated person")
                db.add(foreign_person)
                db.flush()
                foreign_caregiver = CaregiverModel(name="Foreign caregiver", email="foreign-caregiver@example.com", relationship="family", person_id=foreign_person.id, timezone="UTC")
                db.add(foreign_caregiver)
                db.flush()
                foreign_evidence = EvidenceModel(person_id=foreign_person.id, type="note", source_text="Private unrelated note.", uploaded_by_caregiver_id=foreign_caregiver.id, confidence=1.0)
                db.add_all([own_evidence, foreign_evidence])
                db.flush()
                foreign_insight = InsightModel(person_id=foreign_person.id, title="Private insight", description="Private", insight_type="observation", confidence=0.8, evidence_ids=[foreign_evidence.id])
                db.add(foreign_insight)
                db.commit()
                own_evidence_id = own_evidence.id
                foreign_evidence_id = foreign_evidence.id
                foreign_person_id = foreign_person.id
                foreign_caregiver_id = foreign_caregiver.id
                foreign_insight_id = foreign_insight.id
                caregiver_profile_id = caregiver_profile.id
            profile_invitation_response = client.post("/api/v1/auth/caregiver-profile-invitations", headers=headers, json={"person_id": context["person_id"], "caregiver_profile_id": caregiver_profile_id, "permissions": ["read", "write"]})
            assert profile_invitation_response.status_code == 201, profile_invitation_response.text
            profile_invitation_token = profile_invitation_response.json()["invitation_token"]
            profile_linked = client.post("/api/v1/auth/caregiver-profile-invitations/accept", headers=headers, json={"token": profile_invitation_token})
            assert profile_linked.status_code == 200, profile_linked.text
            assert profile_linked.json()["caregiver_profile_id"] == caregiver_profile_id
            profile_invitation_replay = client.post("/api/v1/auth/caregiver-profile-invitations/accept", headers=headers, json={"token": profile_invitation_token})
            assert profile_invitation_replay.status_code == 400, profile_invitation_replay.text
            linked_profile_observation = client.post("/api/v1/caregiver-observations", headers=headers, json={"person_id": context["person_id"], "caregiver_id": caregiver_profile_id, "observation_content": "Verified caregiver observation", "observation_type": "direct"})
            assert linked_profile_observation.status_code == 200, linked_profile_observation.text
            invitation_response = client.post("/api/v1/auth/caregiver-invitations", headers=headers, json={"person_id": context["person_id"], "caregiver_id": caregiver.id, "permissions": ["read", "write"]})
            assert invitation_response.status_code == 201, invitation_response.text
            invitation_token = invitation_response.json()["invitation_token"]
            linked = client.post("/api/v1/auth/caregiver-invitations/accept", headers=headers, json={"token": invitation_token})
            assert linked.status_code == 200, linked.text
            assert linked.json()["caregiver_id"] == caregiver.id
            replayed_invitation = client.post("/api/v1/auth/caregiver-invitations/accept", headers=headers, json={"token": invitation_token})
            assert replayed_invitation.status_code == 400, replayed_invitation.text
            safety_candidate = {"source_evidence_id": own_evidence_id, "source_passage": "Call the clinic tomorrow.", "original_text": "Call the clinic tomorrow.", "extraction_confidence": 0.8, "normalized_action": "call clinic"}
            own_safety_check = client.post("/api/v1/actions/actions/safety-check", headers=headers, json=safety_candidate)
            assert own_safety_check.status_code == 200, own_safety_check.text
            foreign_candidate = dict(safety_candidate, source_evidence_id=foreign_evidence_id)
            foreign_safety_check = client.post("/api/v1/actions/actions/safety-check", headers=headers, json=foreign_candidate)
            assert foreign_safety_check.status_code == 403, foreign_safety_check.text
            foreign_safety_validation = client.post("/api/v1/safety/validate-response", headers=headers, params={"candidate_response": "A summary", "risk_class": "low_risk", "person_id": foreign_person_id}, json={"claims": ["claim"], "evidence_ids": [foreign_evidence_id]})
            assert foreign_safety_validation.status_code == 403, foreign_safety_validation.text
            foreign_safety_event = client.post("/api/v1/safety/events", headers=headers, params={"event_type": "invariant_violation", "person_id": context["person_id"], "subject_type": "evidence", "subject_id": own_evidence_id, "description": "cross-record evidence regression"}, json={"violations": [], "policy_references": [], "input_claims": [], "evidence_ids": [foreign_evidence_id]})
            assert foreign_safety_event.status_code == 400, foreign_safety_event.text
            foreign_safety_escalation = client.post("/api/v1/safety/escalations", headers=headers, params={"person_id": context["person_id"], "affected_person_id": context["person_id"], "trigger": "cross-record evidence regression", "risk_category": "low_risk", "recommended_reviewer": "caregiver"}, json=[foreign_evidence_id])
            assert foreign_safety_escalation.status_code == 400, foreign_safety_escalation.text
            foreign_uploader_write = client.post("/api/v1/evidence", headers=headers, json={"person_id": context["person_id"], "type": "note", "source_text": "forged uploader", "uploaded_by_caregiver_id": foreign_caregiver_id})
            assert foreign_uploader_write.status_code == 400, foreign_uploader_write.text
            foreign_evidence_event = client.post("/api/v1/events", headers=headers, json={"person_id": context["person_id"], "event_type": "observation", "occurred_at": "2026-10-20T09:00:00Z", "title": "Cross-record evidence attempt", "evidence_ids": [foreign_evidence_id], "created_by_caregiver_id": caregiver.id})
            assert foreign_evidence_event.status_code == 400, foreign_evidence_event.text
            foreign_evidence_insight = client.post("/api/v1/insights", headers=headers, json={"person_id": context["person_id"], "title": "Cross-record insight attempt", "description": "test", "insight_type": "observation", "confidence": 0.5, "evidence_ids": [foreign_evidence_id]})
            assert foreign_evidence_insight.status_code == 400, foreign_evidence_insight.text
            foreign_caregiver_correction = client.post("/api/v1/corrections", headers=headers, json={"person_id": context["person_id"], "target_type": "insight", "target_id": foreign_insight_id, "original_text": "old", "corrected_text": "new", "caregiver_id": foreign_caregiver_id})
            assert foreign_caregiver_correction.status_code == 400, foreign_caregiver_correction.text
            foreign_insight_correction = client.post("/api/v1/corrections", headers=headers, json={"person_id": context["person_id"], "target_type": "insight", "target_id": foreign_insight_id, "original_text": "old", "corrected_text": "new", "caregiver_id": caregiver.id})
            assert foreign_insight_correction.status_code == 400, foreign_insight_correction.text
            foreign_evidence_observation = client.post("/api/v1/observations", headers=headers, json={"person_id": context["person_id"], "observed_at": "2026-10-20T09:00:00Z", "original_text": "Cross-record observation", "caregiver_id": caregiver.id, "evidence_ids": [foreign_evidence_id]})
            assert foreign_evidence_observation.status_code == 400, foreign_evidence_observation.text
            foreign_intelligence_observation = client.post("/api/v1/caregiver-observations", headers=headers, json={"person_id": context["person_id"], "caregiver_id": foreign_caregiver_id, "observation_content": "Forged caregiver observation", "observation_type": "direct"})
            assert foreign_intelligence_observation.status_code == 400, foreign_intelligence_observation.text
            foreign_promoter_observation = client.post("/api/v1/caregiver-observations", headers=headers, json={"person_id": context["person_id"], "caregiver_id": caregiver.id, "promoted_by_caregiver_id": foreign_caregiver_id, "observation_content": "Forged promotion attribution", "observation_type": "direct"})
            assert foreign_promoter_observation.status_code == 400, foreign_promoter_observation.text
            foreign_evidence_memory = client.post("/api/v1/reasoning-memory", headers=headers, json={"person_id": context["person_id"], "memory_type": "fact", "key": "test", "value": ["cross-record"], "confidence": 0.5, "source_evidence_ids": [foreign_evidence_id]})
            assert foreign_evidence_memory.status_code == 400, foreign_evidence_memory.text
            foreign_evidence_appointment = client.post("/api/v1/appointments", headers=headers, json={"person_id": context["person_id"], "title": "Cross-record appointment", "scheduled_at": "2026-10-21T09:00:00Z", "created_by_caregiver_id": caregiver.id, "evidence_ids": [foreign_evidence_id]})
            assert foreign_evidence_appointment.status_code == 400, foreign_evidence_appointment.text
            foreign_evidence_transition = client.post("/api/v1/transitions", headers=headers, json={"person_id": context["person_id"], "to_location": "Clinic", "transition_type": "visit", "occurred_at": "2026-10-20T09:00:00Z", "created_by_caregiver_id": caregiver.id, "evidence_ids": [foreign_evidence_id]})
            assert foreign_evidence_transition.status_code == 400, foreign_evidence_transition.text
            foreign_evidence_temporal_fact = client.post("/api/v1/temporal/facts", headers=headers, json={"person_id": context["person_id"], "subject_type": "person", "subject_id": context["person_id"], "evidence_ids": [foreign_evidence_id]})
            assert foreign_evidence_temporal_fact.status_code == 400, foreign_evidence_temporal_fact.text
            action_create = client.post("/api/v1/actions/actions", headers=headers, json={
                "person_id": context["person_id"],
                "source_evidence_id": own_evidence_id,
                "source_passage": "Call the clinic tomorrow.",
                "normalized_action": "call the clinic",
                "original_text": "Call the clinic tomorrow.",
                "created_by_caregiver_id": caregiver.id,
                "actor_type": "caregiver",
                "actor_id": caregiver.id,
            })
            assert action_create.status_code == 200, action_create.text
            created_action_id = action_create.json()["id"]
            outsider_signup = client.post("/api/v1/auth/signup", json={
                "email": "outsider@example.com",
                "password": "another-long-password-2026",
                "display_name": "Outsider",
            })
            assert outsider_signup.status_code == 201, outsider_signup.text
            outsider_login = client.post("/api/v1/auth/login", json={
                "email": "outsider@example.com",
                "password": "another-long-password-2026",
            })
            assert outsider_login.status_code == 200, outsider_login.text
            outsider_headers = {"Authorization": f"Bearer {outsider_login.json()['access_token']}"}
            outsider_action_read = client.get("/api/v1/actions/actions/detail/" + created_action_id, headers=outsider_headers)
            assert outsider_action_read.status_code == 403, outsider_action_read.text
            outsider_action_update = client.patch("/api/v1/actions/actions/" + created_action_id, headers=outsider_headers, json={"normalized_action": "forged change"})
            assert outsider_action_update.status_code == 403, outsider_action_update.text
            outsider_action_cancel = client.post("/api/v1/actions/actions/" + created_action_id + "/cancel", headers=outsider_headers)
            assert outsider_action_cancel.status_code == 403, outsider_action_cancel.text
            staff_only = client.get("/api/v1/safety/policy-rules", headers=headers)
            assert staff_only.status_code == 403, staff_only.text
            own_legacy_person = client.get("/api/v1/persons/" + context["person_id"], headers=headers)
            assert own_legacy_person.status_code == 200, own_legacy_person.text
            profile_create = client.post("/api/v1/caregiver-profiles", headers=headers, json={"person_id": context["person_id"], "name": "Second Caregiver", "email": "second@example.com", "relationship": "daughter"})
            assert profile_create.status_code == 200, profile_create.text
            own_profiles = client.get("/api/v1/caregiver-profiles/" + context["person_id"], headers=headers)
            assert own_profiles.status_code == 200, own_profiles.text
            assert len(own_profiles.json()) == 1
            email_lookup = client.get("/api/v1/caregivers/by-email", headers=headers, params={"person_id": context["person_id"], "email": "second@example.com"})
            assert email_lookup.status_code == 200, email_lookup.text
            own_gaps = client.get("/api/v1/uncertainty/gaps/" + context["person_id"], headers=headers)
            assert own_gaps.status_code == 200, own_gaps.text
            assert own_gaps.json() == []
            own_baselines = client.get("/api/v1/change/baselines/" + context["person_id"], headers=headers)
            assert own_baselines.status_code == 200, own_baselines.text
            assert own_baselines.json() == []
            own_hypotheses = client.get("/api/v1/causal/hypotheses/" + context["person_id"], headers=headers)
            assert own_hypotheses.status_code == 200, own_hypotheses.text
            assert own_hypotheses.json() == []

            guard = require_person_access("read")
            with TestingSession() as db:
                account = db.query(AuthAccount).filter_by(email="caregiver@example.com").one()
                allowed = guard(person_id=context["person_id"], account=account, db=db)
                assert allowed.id == account.id
                unrelated = Person(name="Unrelated Person")
                db.add(unrelated)
                db.commit()
                db.refresh(unrelated)
                try:
                    guard(person_id=unrelated.id, account=account, db=db)
                    assert False, "Cross-person access should be denied"
                except HTTPException as exc:
                    assert exc.status_code == 403
            unrelated_legacy_person = client.get("/api/v1/persons/" + unrelated.id, headers=headers)
            assert unrelated_legacy_person.status_code == 403, unrelated_legacy_person.text
            unrelated_action_read = client.get("/api/v1/actions/actions/" + unrelated.id, headers=headers)
            assert unrelated_action_read.status_code == 403, unrelated_action_read.text
            unrelated_situation_write = client.post("/api/situation", headers=headers, json={"person_id": unrelated.id, "caregiver_id": "not-a-real-caregiver", "raw_input": "test"})
            assert unrelated_situation_write.status_code == 403, unrelated_situation_write.text
            unrelated_caregiver_read = client.get("/api/v1/caregiver-profiles/" + unrelated.id, headers=headers)
            assert unrelated_caregiver_read.status_code == 403, unrelated_caregiver_read.text
            unrelated_safety_read = client.get("/api/v1/safety/audit/" + unrelated.id, headers=headers)
            assert unrelated_safety_read.status_code == 403, unrelated_safety_read.text
            unrelated_email_lookup = client.get("/api/v1/caregivers/by-email", headers=headers, params={"person_id": unrelated.id, "email": "second@example.com"})
            assert unrelated_email_lookup.status_code == 403, unrelated_email_lookup.text
            unrelated_write = client.post("/api/v1/care-windows", headers=headers, params={"person_id": unrelated.id, "event_type": "test", "occurred_at": "not-a-date"})
            assert unrelated_write.status_code == 403, unrelated_write.text
            unrelated_read = client.get("/api/v1/uncertainty/gaps/" + unrelated.id, headers=headers)
            assert unrelated_read.status_code == 403, unrelated_read.text
            unrelated_baselines = client.get("/api/v1/change/baselines/" + unrelated.id, headers=headers)
            assert unrelated_baselines.status_code == 403, unrelated_baselines.text
            unrelated_hypotheses = client.get("/api/v1/causal/hypotheses/" + unrelated.id, headers=headers)
            assert unrelated_hypotheses.status_code == 403, unrelated_hypotheses.text

            logout = client.post("/api/v1/auth/logout", headers=headers)
            assert logout.status_code == 204
            after_logout = client.get("/api/v1/auth/me", headers=headers)
            assert after_logout.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine, tables=[ActionModel.__table__, SafetyEventModel.__table__, SafetyAuditRecordModel.__table__, CaregiverProfileModel.__table__, CausalHypothesis.__table__, Baseline.__table__, InformationGap.__table__, AccountPersonAccess.__table__, CaregiverProfileInvitation.__table__, CaregiverProfileAccountLink.__table__, CaregiverObservationModel.__table__, CaregiverInvitation.__table__, CaregiverAccountLink.__table__, AuthSession.__table__, AuthAccount.__table__, InsightModel.__table__, EvidenceModel.__table__, CaregiverModel.__table__, Person.__table__])
        engine.dispose()
