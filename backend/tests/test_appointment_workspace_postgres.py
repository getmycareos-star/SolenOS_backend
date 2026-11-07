"""Real PostgreSQL integration for the complete appointment continuity lifecycle."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import hashlib
import json
import os
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.time_location import Appointment
from app.models.uncertainty import Contradiction
from app.models.anonymous_session import AnonymousSession
from app.models.care import Caregiver
from app.models.collaboration import ConsentRecord

DATABASE_URL = os.environ.get('DATABASE_URL', '')
pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith(('postgresql://', 'postgresql+psycopg2://', 'postgresql+psycopg://')),
    reason='requires the dedicated PostgreSQL migration-validation database',
)


def test_postgres_appointment_outcome_evidence_followup_and_account_recovery():
    suffix = uuid4().hex
    with TestClient(app) as client:
        owner = client.post('/api/v1/sessions/anonymous', json={'caregiver_name': 'PostgreSQL caregiver'})
        other = client.post('/api/v1/sessions/anonymous', json={'caregiver_name': 'Other caregiver'})
        assert owner.status_code == 201, owner.text
        assert other.status_code == 201, other.text
        headers = {'Authorization': 'Bearer ' + owner.json()['access_token']}
        person_id = owner.json()['person']['id']
        caregiver_id = owner.json()['caregiver']['id']
        db = SessionLocal()
        appointment = Appointment(
            person_id=person_id,
            title='PostgreSQL appointment continuity test',
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
            provider='Integration clinic',
            status='scheduled',
            created_by_caregiver_id=caregiver_id,
        )
        db.add(appointment)
        db.commit()
        appointment_id = appointment.id
        db.close()

        actual_visit = datetime.now(timezone.utc).isoformat()
        note = 'Caregiver reports the professional asked for a review next month.'
        create_body = {
            'appointment_id': appointment_id,
            'status': 'outcome_pending',
            'idempotency_key': 'appointment-create-' + suffix,
            'payload': {'attendanceStatus': 'attended', 'actualVisitAt': actual_visit, 'clinicianFindings': note},
            'change_reason': 'Record appointment outcome',
        }
        created = client.post('/api/v1/appointment-workspaces', headers=headers, json=create_body)
        replay = client.post('/api/v1/appointment-workspaces', headers=headers, json=create_body)
        assert created.status_code == 201, created.text
        assert replay.status_code == 201, replay.text
        assert replay.json()['id'] == created.json()['id']
        record_id = created.json()['id']

        db = SessionLocal()
        linked_appointment = db.query(Appointment).filter(Appointment.id == appointment_id).one()
        assert linked_appointment.status == 'completed'
        assert linked_appointment.completed_at is not None
        db.close()

        update_body = {
            'expected_version': 1,
            'idempotency_key': 'appointment-update-' + suffix,
            'status': 'outcome_pending',
            'payload': {'attendanceStatus': 'attended', 'actualVisitAt': actual_visit, 'clinicianFindings': note, 'unanswered': 'When should the next review happen?'},
            'change_reason': 'Add unanswered question',
        }
        updated = client.patch('/api/v1/appointment-workspaces/' + record_id, headers=headers, json=update_body)
        update_replay = client.patch('/api/v1/appointment-workspaces/' + record_id, headers=headers, json=update_body)
        assert updated.status_code == 200, updated.text
        assert update_replay.status_code == 200, update_replay.text
        assert update_replay.json()['version_number'] == 2
        changed_retry = {**update_body, 'payload': {**update_body['payload'], 'unanswered': 'Changed after save'}}
        assert client.patch('/api/v1/appointment-workspaces/' + record_id, headers=headers, json=changed_retry).status_code == 409

        evidence_body = {
            'person_id': person_id,
            'type': 'appointment_note',
            'evidence_kind': 'caregiver_report',
            'epistemic_status': 'caregiver_reported',
            'source_text': note,
            'source_type': 'caregiver_report_of_professional_statement',
            'reported_by_label': 'Caregiver',
            'reported_by_caregiver_id': caregiver_id,
            'uploaded_by_caregiver_id': caregiver_id,
            'occurred_at': actual_visit,
            'collection_method': 'manual',
            'ingestion_key': 'appointment-evidence-' + suffix,
        }
        evidence = client.post('/api/v1/evidence', headers=headers, json=evidence_body)
        evidence_replay = client.post('/api/v1/evidence', headers=headers, json=evidence_body)
        assert evidence.status_code == 200, evidence.text
        assert evidence_replay.status_code == 200, evidence_replay.text
        assert evidence_replay.json()['id'] == evidence.json()['id']

        assertion_body = {
            'subject_type': 'appointment',
            'subject_id': record_id,
            'field': 'clinicianFindings',
            'assertion_kind': 'time_bound_observation',
            'asserted_value': note,
            'source_label': 'Caregiver-reported professional statement',
            'source_text': note,
            'evidence_ids': [evidence.json()['id']],
            'observed_at': actual_visit,
        }
        assertion = client.post('/api/v1/care-continuity/assertions', headers=headers, json=assertion_body)
        assertion_replay = client.post('/api/v1/care-continuity/assertions', headers=headers, json=assertion_body)
        assert assertion.status_code == 201, assertion.text
        assert assertion_replay.status_code == 201, assertion_replay.text
        assert assertion_replay.json()['id'] == assertion.json()['id']
        accepted = client.patch('/api/v1/care-continuity/assertions/' + assertion.json()['id'] + '/status', headers=headers, json={'assertion_status': 'accepted'})
        assert accepted.status_code == 200, accepted.text

        second_note = 'A second caregiver account records a different recollection of the professional instruction.'
        second_evidence = client.post('/api/v1/evidence', headers=headers, json={**evidence_body, 'source_text': second_note, 'ingestion_key': 'appointment-conflict-' + suffix})
        assert second_evidence.status_code == 200, second_evidence.text
        conflict_assertion = client.post('/api/v1/care-continuity/assertions', headers=headers, json={**assertion_body, 'asserted_value': second_note, 'source_text': second_note, 'evidence_ids': [second_evidence.json()['id']]})
        assert conflict_assertion.status_code == 201, conflict_assertion.text
        db = SessionLocal()
        contradiction = db.query(Contradiction).filter(Contradiction.person_id == person_id, Contradiction.subject_type == 'appointment', Contradiction.subject_id == record_id, Contradiction.field == 'clinicianFindings', Contradiction.resolution_status == 'unresolved').one_or_none()
        assert contradiction is not None
        assert set(contradiction.source_assertion_ids) == {assertion.json()['id'], conflict_assertion.json()['id']}
        db.close()

        action = client.post('/api/v1/registered-actions/' + person_id + '/approve', headers=headers, json={
            'explicit_approval': True,
            'idempotency_key': 'appointment-action-' + suffix,
            'suggestion_id': 'appointment-' + suffix + '-follow-up',
            'title': 'Call the clinic for the next review date',
            'action_type': 'follow_up',
            'rationale': 'Caregiver-approved next step after the appointment.',
            'limitations': 'The named responsible person is a note, not an account assignment.',
            'evidence_ids': [evidence.json()['id']],
            'create_reminder': False,
        })
        assert action.status_code == 201, action.text

        target = Caregiver(
            id='postgres-follow-up-recipient-' + suffix,
            name='Second PostgreSQL caregiver',
            email='postgres-follow-up-' + suffix + '@example.com',
            relationship='Family',
            person_id=person_id,
        )
        now = datetime.now(timezone.utc)
        db = SessionLocal()
        db.add(target)
        db.flush()
        for consent_caregiver_id in (caregiver_id, target.id):
            db.add(ConsentRecord(
                person_id=person_id,
                caregiver_id=consent_caregiver_id,
                medical_disclaimer_acknowledged=True,
                privacy_model_acknowledged=True,
                multi_caregiver_acknowledged=True,
                no_advertising_acknowledged=True,
                accepted_at=now,
            ))
        target_token = 'postgres-follow-up-token-' + suffix
        db.add(AnonymousSession(
            person_id=person_id,
            caregiver_id=target.id,
            token_hash=hashlib.sha256(target_token.encode()).hexdigest(),
            expires_at=now + timedelta(days=30),
        ))
        target_id = target.id
        db.commit()
        db.close()
        target_headers = {'Authorization': 'Bearer ' + target_token}

        handoff = client.post('/api/v1/handoffs', headers={**headers, 'X-Request-ID': 'postgres-handoff-' + suffix}, json={
            'person_id': person_id,
            'recipient_caregiver_ids': [target_id],
            'scope': ['open_loops'],
        })
        assert handoff.status_code == 201, handoff.text
        handoff_id = handoff.json()['id']
        summary = client.put('/api/v1/handoffs/' + handoff_id + '/summary', headers=headers, json={
            'sections': {'open_loops': 'Call the clinic for the next review date'},
            'source_record_ids': [],
            'caregiver_reviewed': True,
            'prepared_at': now.isoformat(),
        })
        assert summary.status_code == 200, summary.text
        accepted_handoff = client.post('/api/v1/handoffs/' + handoff_id + '/accept', headers=target_headers)
        assert accepted_handoff.status_code == 200, accepted_handoff.text
        assert accepted_handoff.json()['status'] == 'accepted'

        reassigned = client.patch('/api/v1/registered-actions/' + action.json()['id'] + '/assignee', headers=headers, json={
            'target_caregiver_id': target_id,
            'handoff_id': handoff_id,
            'expected_version': action.json()['version'],
            'idempotency_key': 'postgres-assignment-' + suffix,
            'reason': 'The receiving caregiver accepted the follow-up handoff.',
        })
        assert reassigned.status_code == 200, reassigned.text
        assert reassigned.json()['responsible_caregiver_id'] == target_id
        assignment_replay = client.patch('/api/v1/registered-actions/' + action.json()['id'] + '/assignee', headers=headers, json={
            'target_caregiver_id': target_id,
            'handoff_id': handoff_id,
            'expected_version': action.json()['version'],
            'idempotency_key': 'postgres-assignment-' + suffix,
            'reason': 'The receiving caregiver accepted the follow-up handoff.',
        })
        assert assignment_replay.status_code == 200, assignment_replay.text
        assert assignment_replay.json()['version'] == reassigned.json()['version']
        assignment_history = client.get('/api/v1/registered-actions/' + action.json()['id'] + '/history', headers=headers)
        assert assignment_history.status_code == 200, assignment_history.text
        assert any(item.get('handoff_id') == handoff_id and item.get('new_responsible_caregiver_id') == target_id for item in assignment_history.json())

        progressed = client.patch('/api/v1/registered-actions/' + action.json()['id'] + '/status', headers=target_headers, json={
            'status': 'in_progress',
            'expected_version': reassigned.json()['version'],
            'reason': 'Assigned caregiver started the follow-up.',
        })
        assert progressed.status_code == 200, progressed.text
        unauthorized = client.post('/api/v1/care-continuity/action-outcomes', headers=headers, json={'action_id': action.json()['id'], 'action_type': 'follow_up_result', 'outcome_status': 'result_received', 'notes': 'Owner is no longer assigned.', 'evidence_ids': ['x'], 'idempotency_key': 'postgres-denied-' + suffix})
        assert unauthorized.status_code == 403, unauthorized.text
        other_headers = {'Authorization': 'Bearer ' + other.json()['access_token']}
        foreign_evidence = client.post('/api/v1/evidence', headers=other_headers, json={
            'person_id': other.json()['person']['id'],
            'type': 'appointment_result',
            'evidence_kind': 'caregiver_report',
            'epistemic_status': 'caregiver_reported',
            'source_text': 'Evidence belonging to another care record.',
            'source_type': 'caregiver_recorded_received_result',
            'reported_by_label': 'Other caregiver',
            'reported_by_caregiver_id': other.json()['caregiver']['id'],
            'uploaded_by_caregiver_id': other.json()['caregiver']['id'],
            'collection_method': 'manual',
            'ingestion_key': 'postgres-foreign-result-' + suffix,
        })
        assert foreign_evidence.status_code == 200, foreign_evidence.text
        cross_record_result = client.post('/api/v1/care-continuity/action-outcomes', headers=target_headers, json={
            'action_id': action.json()['id'],
            'action_type': 'follow_up_result',
            'outcome_status': 'result_received',
            'notes': 'Foreign evidence must not be linked.',
            'evidence_ids': [foreign_evidence.json()['id']],
            'idempotency_key': 'postgres-cross-record-result-' + suffix,
        })
        assert cross_record_result.status_code == 404, cross_record_result.text

        result_text = 'The clinic sent the requested result; the caregiver recorded it for clinician review.'
        result_evidence = client.post('/api/v1/evidence', headers=target_headers, json={
            'person_id': person_id,
            'type': 'appointment_result',
            'evidence_kind': 'caregiver_report',
            'epistemic_status': 'caregiver_reported',
            'source_text': result_text,
            'source_type': 'caregiver_recorded_received_result',
            'reported_by_label': 'Second family caregiver',
            'reported_by_caregiver_id': target_id,
            'uploaded_by_caregiver_id': target_id,
            'collection_method': 'manual',
            'ingestion_key': 'postgres-result-evidence-' + suffix,
        })
        assert result_evidence.status_code == 200, result_evidence.text
        received_body = {
            'action_id': action.json()['id'],
            'action_type': 'follow_up_result',
            'outcome_status': 'result_received',
            'notes': result_text,
            'evidence_ids': [result_evidence.json()['id']],
            'idempotency_key': 'postgres-result-received-' + suffix,
        }
        received = client.post('/api/v1/care-continuity/action-outcomes', headers=target_headers, json=received_body)
        received_replay = client.post('/api/v1/care-continuity/action-outcomes', headers=target_headers, json=received_body)
        assert received.status_code == 201, received.text
        assert received_replay.status_code == 201, received_replay.text
        assert received_replay.json()['id'] == received.json()['id']
        assert received.json()['received_by_caregiver_id'] == target_id
        assert received.json()['received_at'] is not None

        reviewed = client.post('/api/v1/care-continuity/action-outcomes', headers=target_headers, json={
            'action_id': action.json()['id'],
            'action_type': 'follow_up_result',
            'outcome_status': 'result_reviewed',
            'related_outcome_id': received.json()['id'],
            'review_decision': 'needs_clinician_review',
            'notes': 'The caregiver reviewed the result and recorded that a clinician must interpret it.',
            'evidence_ids': [result_evidence.json()['id']],
            'idempotency_key': 'postgres-result-reviewed-' + suffix,
        })
        assert reviewed.status_code == 201, reviewed.text
        assert reviewed.json()['reviewed_by_caregiver_id'] == target_id
        assert reviewed.json()['reviewed_at'] is not None
        assert reviewed.json()['related_outcome_id'] == received.json()['id']
        assert reviewed.json()['review_decision'] == 'needs_clinician_review'
        assert reviewed.json()['evidence_ids'] == [result_evidence.json()['id']]

        assignment_audit_id = next(item['id'] for item in assignment_history.json() if item.get('handoff_id') == handoff_id)
        db = SessionLocal()
        bind = db.get_bind()
        db.close()
        for statement, params in (
            ('UPDATE action_lifecycle_audit SET reason = :value WHERE id = :id', {'value': 'tampered', 'id': assignment_audit_id}),
            ('UPDATE care_action_outcomes SET notes = :value WHERE id = :id', {'value': 'tampered', 'id': received.json()['id']}),
        ):
            with bind.connect() as connection:
                try:
                    connection.execute(text(statement), params)
                    connection.commit()
                except DBAPIError as exc:
                    connection.rollback()
                    assert 'append-only' in str(exc).lower(), str(exc)
                else:
                    raise AssertionError('PostgreSQL allowed mutation of append-only care history')

        new_device = TestClient(app)
        try:
            recovered = new_device.get('/api/v1/appointment-workspaces/' + record_id, headers=headers)
            assert recovered.status_code == 200, recovered.text
            assert recovered.json()['payload']['clinicianFindings'] == note
            revisions = new_device.get('/api/v1/appointment-workspaces/' + record_id + '/revisions', headers=headers)
            assert revisions.status_code == 200, revisions.text
            assert [item['revision_number'] for item in revisions.json()['items']] == [1, 2]
            persisted_outcomes = new_device.get('/api/v1/care-continuity/outcomes/' + person_id, headers=target_headers)
            assert persisted_outcomes.status_code == 200, persisted_outcomes.text
            events = [item for item in persisted_outcomes.json() if item.get('action_id') == action.json()['id']]
            assert {item['outcome_status'] for item in events} >= {'result_received', 'result_reviewed'}
            assert any(item.get('id') == received.json()['id'] for item in events)
            assert any(item.get('id') == reviewed.json()['id'] for item in events)
        finally:
            new_device.close()
        other_headers = {'Authorization': 'Bearer ' + other.json()['access_token']}
        denied = client.get('/api/v1/appointment-workspaces/' + record_id, headers=other_headers)
        assert denied.status_code == 404, denied.text
        other_evidence = client.get('/api/v1/evidence/' + other.json()['person']['id'], headers=other_headers)
        assert other_evidence.status_code == 200, other_evidence.text
        assert note not in other_evidence.text
        other_outcomes = client.get('/api/v1/care-continuity/outcomes/' + other.json()['person']['id'], headers=other_headers)
        assert other_outcomes.status_code == 200, other_outcomes.text
        assert not any(item.get('action_id') == action.json()['id'] for item in other_outcomes.json())

def _latency_percentiles(samples_ms: list[float]) -> dict[str, float]:
    ordered = sorted(samples_ms)
    def percentile(p: float) -> float:
        position = (len(ordered) - 1) * p
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)
    return {"p50": percentile(0.50), "p95": percentile(0.95), "p99": percentile(0.99)}


def test_postgres_daily_capture_and_appointment_write_latency(monkeypatch):
    """Measure authenticated persistence against isolated PostgreSQL without model inference."""
    from app.api import v1 as api_v1

    def skip_model_review(*args, **kwargs):
        raise RuntimeError("Qwen inference is deliberately excluded from the database-write microbenchmark")

    monkeypatch.setattr(api_v1, "extract_review_candidates", skip_model_review)
    suffix = uuid4().hex
    sample_count = 20
    daily_write_ms: list[float] = []
    appointment_schedule_create_ms: list[float] = []
    appointment_lifecycle_patch_ms: list[float] = []
    appointment_list_read_ms: list[float] = []
    handoff_inbox_read_ms: list[float] = []
    appointment_workspace_create_ms: list[float] = []
    appointment_workspace_update_ms: list[float] = []

    with TestClient(app) as client:
        owner = client.post('/api/v1/sessions/anonymous', json={'caregiver_name': 'PostgreSQL performance fixture'})
        other = client.post('/api/v1/sessions/anonymous', json={'caregiver_name': 'Other PostgreSQL fixture'})
        assert owner.status_code == 201, owner.text
        assert other.status_code == 201, other.text
        headers = {'Authorization': 'Bearer ' + owner.json()['access_token']}
        person_id = owner.json()['person']['id']
        caregiver_id = owner.json()['caregiver']['id']
        other_headers = {'Authorization': 'Bearer ' + other.json()['access_token']}

        daily_payloads = []
        daily_receipts = []
        for index in range(sample_count):
            payload = {
                'person_id': person_id,
                'caregiver_id': caregiver_id,
                'medications': [],
                'vitals': [],
                'scales': [],
                'notes': f'Isolated PostgreSQL Daily Capture benchmark fixture {index} {suffix}.',
                'capture_idempotency_key': f'daily-capture-benchmark-{suffix}-{index}',
            }
            daily_payloads.append(payload)
            started = time.perf_counter()
            response = client.post('/api/v1/daily/log', headers=headers, json=payload)
            daily_write_ms.append((time.perf_counter() - started) * 1000)
            assert response.status_code == 200, response.text
            assert response.json()['success'] is True
            assert response.json()['idempotent_replay'] is False
            assert len(response.json()['evidence_ids']) == 1
            assert response.json()['qwen_extraction_status'] == 'unavailable'
            daily_receipts.append(response.json())

        replay = client.post('/api/v1/daily/log', headers=headers, json=daily_payloads[0])
        assert replay.status_code == 200, replay.text
        assert replay.json()['id'] == daily_receipts[0]['id']
        assert replay.json()['idempotent_replay'] is True
        changed = client.post(
            '/api/v1/daily/log',
            headers=headers,
            json={**daily_payloads[0], 'notes': 'Changed payload under the same idempotency key.'},
        )
        assert changed.status_code == 409, changed.text

        appointment_ids = []
        for index in range(sample_count):
            appointment_body = {
                'person_id': person_id,
                'title': f'Isolated appointment scheduling fixture {index} {suffix}',
                'scheduled_at': (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                'created_by_caregiver_id': caregiver_id,
                'status': 'scheduled',
            }
            started = time.perf_counter()
            scheduled = client.post('/api/v1/appointments', headers=headers, json=appointment_body)
            appointment_schedule_create_ms.append((time.perf_counter() - started) * 1000)
            assert scheduled.status_code == 200, scheduled.text
            appointment_ids.append(scheduled.json()['id'])

            started = time.perf_counter()
            lifecycle = client.patch(
                '/api/v1/appointments/' + scheduled.json()['id'] + '/lifecycle',
                headers=headers,
            )
            appointment_lifecycle_patch_ms.append((time.perf_counter() - started) * 1000)
            assert lifecycle.status_code == 200, lifecycle.text
            assert lifecycle.json()['status'] == 'upcoming'

        for _ in range(sample_count):
            started = time.perf_counter()
            owner_appointments = client.get('/api/v1/appointments/' + person_id, headers=headers)
            appointment_list_read_ms.append((time.perf_counter() - started) * 1000)
            assert owner_appointments.status_code == 200, owner_appointments.text
            assert len(owner_appointments.json()) == sample_count
        assert client.get('/api/v1/appointments/' + person_id).status_code == 401
        assert client.get('/api/v1/appointments/' + person_id, headers=other_headers).status_code == 403
        unauthenticated_create = client.post(
            '/api/v1/appointments',
            json={
                'person_id': person_id,
                'title': 'Unauthenticated appointment write must be denied',
                'scheduled_at': datetime.now(timezone.utc).isoformat(),
                'created_by_caregiver_id': caregiver_id,
            },
        )
        assert unauthenticated_create.status_code == 401, unauthenticated_create.text
        unauthenticated_lifecycle = client.patch(
            '/api/v1/appointments/' + appointment_ids[0] + '/lifecycle',
        )
        assert unauthenticated_lifecycle.status_code == 401, unauthenticated_lifecycle.text
        foreign_create = client.post(
            '/api/v1/appointments',
            headers=other_headers,
            json={
                'person_id': person_id,
                'title': 'Cross-person appointment write must be denied',
                'scheduled_at': datetime.now(timezone.utc).isoformat(),
                'created_by_caregiver_id': caregiver_id,
            },
        )
        assert foreign_create.status_code == 403, foreign_create.text
        foreign_lifecycle = client.patch(
            '/api/v1/appointments/' + appointment_ids[0] + '/lifecycle',
            headers=other_headers,
        )
        assert foreign_lifecycle.status_code == 403, foreign_lifecycle.text

        for index in range(sample_count):
            create_body = {
                'appointment_id': None,
                'status': 'preparation',
                'idempotency_key': f'appointment-benchmark-create-{suffix}-{index}',
                'payload': {
                    'visitPurpose': f'Isolated appointment workspace fixture {index}',
                    'attendanceStatus': 'not_recorded',
                },
                'change_reason': 'Disposable PostgreSQL write benchmark',
            }
            started = time.perf_counter()
            created = client.post('/api/v1/appointment-workspaces', headers=headers, json=create_body)
            appointment_workspace_create_ms.append((time.perf_counter() - started) * 1000)
            assert created.status_code == 201, created.text

            update_body = {
                'expected_version': 1,
                'status': 'preparation',
                'idempotency_key': f'appointment-benchmark-update-{suffix}-{index}',
                'payload': {
                    **create_body['payload'],
                    'unanswered': f'Benchmark follow-up clarification {index}',
                },
                'change_reason': 'Disposable PostgreSQL update benchmark',
            }
            started = time.perf_counter()
            updated = client.patch(
                '/api/v1/appointment-workspaces/' + created.json()['id'],
                headers=headers,
                json=update_body,
            )
            appointment_workspace_update_ms.append((time.perf_counter() - started) * 1000)
            assert updated.status_code == 200, updated.text
            assert updated.json()['version_number'] == 2

        # Exercise the consent-aware handoff flow with a second caregiver linked
        # to this same care record. Consent is accepted through the public API.
        recipient_id = str(uuid4())
        recipient_token = 'postgres-handoff-recipient-' + suffix
        now = datetime.now(timezone.utc)
        db = SessionLocal()
        db.add(Caregiver(
            id=recipient_id,
            name='PostgreSQL handoff recipient',
            relationship='family',
            person_id=person_id,
        ))
        db.flush()
        db.add(AnonymousSession(
            id=str(uuid4()),
            person_id=person_id,
            caregiver_id=recipient_id,
            token_hash=hashlib.sha256(recipient_token.encode('utf-8')).hexdigest(),
            expires_at=now + timedelta(hours=1),
        ))
        db.commit()
        db.close()
        recipient_headers = {'Authorization': 'Bearer ' + recipient_token}
        for consent_headers, consent_caregiver_id in ((headers, caregiver_id), (recipient_headers, recipient_id)):
            consent = client.post('/api/v1/consent', headers=consent_headers, json={
                'person_id': person_id,
                'caregiver_id': consent_caregiver_id,
                'medical_disclaimer_acknowledged': True,
                'privacy_model_acknowledged': True,
                'multi_caregiver_acknowledged': True,
                'no_advertising_acknowledged': True,
                'data_improvement_consent': False,
                'accepted_terms_version': '2026-10',
            })
            assert consent.status_code == 200, consent.text
            assert consent.json()['verified'] is True

        directory = client.get('/api/v1/handoffs/directory/recipients', headers=headers)
        assert directory.status_code == 200, directory.text
        assert any(item['caregiver_id'] == recipient_id and item['can_receive_handoff'] is True for item in directory.json()['items'])

        handoff_create_ms: list[float] = []
        handoff_payloads = []
        handoff_ids = []
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        for index in range(sample_count):
            payload = {
                'person_id': person_id,
                'recipient_caregiver_ids': [recipient_id],
                'scope': ['lcr_summary', 'open_loops'],
                'expires_at': expires_at,
            }
            request_id = f'postgres-handoff-benchmark-{suffix}-{index}'
            handoff_payloads.append((payload, request_id))
            started = time.perf_counter()
            created_handoff = client.post(
                '/api/v1/handoffs',
                headers={**headers, 'X-Request-ID': request_id},
                json=payload,
            )
            handoff_create_ms.append((time.perf_counter() - started) * 1000)
            assert created_handoff.status_code == 201, created_handoff.text
            handoff_ids.append(created_handoff.json()['id'])

        handoff_replay = client.post(
            '/api/v1/handoffs',
            headers={**headers, 'X-Request-ID': handoff_payloads[0][1]},
            json=handoff_payloads[0][0],
        )
        assert handoff_replay.status_code == 201, handoff_replay.text
        assert handoff_replay.json()['id'] == handoff_ids[0]
        handoff_changed_retry = client.post(
            '/api/v1/handoffs',
            headers={**headers, 'X-Request-ID': handoff_payloads[0][1]},
            json={**handoff_payloads[0][0], 'scope': ['lcr_summary']},
        )
        assert handoff_changed_retry.status_code == 409, handoff_changed_retry.text

        for _ in range(sample_count):
            started = time.perf_counter()
            recipient_inbox = client.get('/api/v1/handoffs/inbox', headers=recipient_headers)
            handoff_inbox_read_ms.append((time.perf_counter() - started) * 1000)
            assert recipient_inbox.status_code == 200, recipient_inbox.text
            assert recipient_inbox.json()['count'] == sample_count
            assert {item['id'] for item in recipient_inbox.json()['items']} == set(handoff_ids)

        # A recipient cannot accept before the sender attaches a caregiver-reviewed,
        # person-scoped summary. This verifies the actual two-caregiver flow.
        premature_accept = client.post(
            '/api/v1/handoffs/' + handoff_ids[0] + '/accept',
            headers=recipient_headers,
        )
        assert premature_accept.status_code == 409, premature_accept.text

        summary = client.put(
            '/api/v1/handoffs/' + handoff_ids[0] + '/summary',
            headers=headers,
            json={
                'sections': {'lcr_summary': 'Caregiver-reviewed Daily Capture handoff summary.'},
                'source_record_ids': [daily_receipts[0]['evidence_ids'][0]],
                'caregiver_reviewed': True,
                'prepared_at': datetime.now(timezone.utc).isoformat(),
            },
        )
        assert summary.status_code == 200, summary.text
        assert summary.json()['review_attestation'] == 'client_declared_not_server_verified'

        accepted = client.post(
            '/api/v1/handoffs/' + handoff_ids[0] + '/accept',
            headers=recipient_headers,
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()['status'] == 'accepted'
        assert any(
            item['caregiver_id'] == recipient_id and item['status'] == 'accepted'
            for item in accepted.json()['recipients']
        )

        acknowledgement = client.post(
            '/api/v1/handoffs/' + handoff_ids[0] + '/acknowledge',
            headers=recipient_headers,
            json={'acknowledgement': 'I have received and reviewed this care handoff.'},
        )
        assert acknowledgement.status_code == 200, acknowledgement.text
        assert acknowledgement.json()['acknowledged'] is True
        assert acknowledgement.json()['already_acknowledged'] is False

        # Revoking the sender's required consent must stop new sharing and
        # prevent the recipient from accepting an already-created pending handoff.
        revoked = client.post('/api/v1/consent', headers=headers, json={
            'person_id': person_id,
            'caregiver_id': caregiver_id,
            'action': 'revoke',
        })
        assert revoked.status_code == 200, revoked.text
        denied_after_revoke = client.post(
            '/api/v1/handoffs',
            headers={**headers, 'X-Request-ID': 'postgres-handoff-after-revoke-' + suffix},
            json=handoff_payloads[0][0],
        )
        assert denied_after_revoke.status_code == 403, denied_after_revoke.text
        denied_accept_after_revoke = client.post(
            '/api/v1/handoffs/' + handoff_ids[1] + '/accept',
            headers=recipient_headers,
        )
        assert denied_accept_after_revoke.status_code == 404, denied_accept_after_revoke.text

        denied = client.post('/api/v1/daily/log', headers=other_headers, json=daily_payloads[0])
        assert denied.status_code == 403, denied.text
        foreign_record = client.get(
            '/api/v1/appointment-workspaces/' + created.json()['id'],
            headers=other_headers,
        )
        assert foreign_record.status_code == 404, foreign_record.text

    print(json.dumps({
        'event': 'postgres_care_write_benchmark',
        'sample_count_per_route': sample_count,
        'daily_capture_post_ms': _latency_percentiles(daily_write_ms),
        'appointment_schedule_create_post_ms': _latency_percentiles(appointment_schedule_create_ms),
        'appointment_lifecycle_patch_ms': _latency_percentiles(appointment_lifecycle_patch_ms),
        'appointment_list_get_ms': _latency_percentiles(appointment_list_read_ms),
        'appointment_workspace_create_post_ms': _latency_percentiles(appointment_workspace_create_ms),
        'appointment_workspace_update_patch_ms': _latency_percentiles(appointment_workspace_update_ms),
        'handoff_create_post_ms': _latency_percentiles(handoff_create_ms),
        'handoff_inbox_get_ms': _latency_percentiles(handoff_inbox_read_ms),
        'daily_capture_qwen_inference': 'disabled_for_write_isolation',
        'scope': 'disposable_postgresql_ci_only',
    }, separators=(',', ':')))
