"""Real HTTP/PostgreSQL test for handoffs, reminders, receipts, and attribution."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_URL = "http://127.0.0.1:8014"
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
    raise SystemExit("Refusing coordination E2E: DATABASE_URL must be PostgreSQL.")


def request(method, path, *, body=None, token=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw.decode("utf-8", "replace")
        return exc.code, payload


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def start_server():
    log_path = Path("/tmp/solenos-coordination-e2e-uvicorn.log")
    log = log_path.open("ab")
    server_env = os.environ.copy()
    server_env["ENABLE_REMINDER_SCHEDULER"] = "false"
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8014"],
        cwd=Path(__file__).resolve().parents[1],
        env=server_env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        if process.poll() is not None:
            log.close()
            details = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
            raise RuntimeError(f"Uvicorn exited early.\\n{details}")
        try:
            status, _ = request("GET", "/ready")
            if status == 200:
                log.close()
                return process
        except Exception:
            pass
        time.sleep(0.5)
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    log.close()
    details = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
    raise RuntimeError(f"Uvicorn did not become ready.\\n{details}")


def stop_server(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main():
    server = start_server()
    try:
        status, owner = request(
            "POST", "/api/v1/sessions/anonymous",
            body={"name": "Coordination E2E person A", "caregiver_name": "Coordination E2E caregiver A"},
        )
        require(status == 201, f"owner session bootstrap failed: {status} {owner}")
        token_a = owner["access_token"]
        person_id = owner["person"]["id"]
        caregiver_id = owner["caregiver"]["id"]

        status, other = request(
            "POST", "/api/v1/sessions/anonymous",
            body={"name": "Coordination E2E person B", "caregiver_name": "Coordination E2E caregiver B"},
        )
        require(status == 201, f"second session bootstrap failed: {status} {other}")
        token_b = other["access_token"]

        now = datetime.now(timezone.utc)
        due_at = now + timedelta(hours=1)
        reminder_at = now - timedelta(minutes=2)
        handoff_payload = {
            "person_id": person_id,
            "title": "Coordination E2E handoff",
            "summary": "Confirm the clinic appointment and report the outcome.",
            "outstanding_items": ["Confirm the appointment date"],
            "recent_changes": ["Appointment date is not yet confirmed"],
            "safety_concerns": ["Ask the care team about any new symptoms"],
            "due_at": due_at.isoformat(),
            "reminder_at": reminder_at.isoformat(),
            "access_scope": "care_circle",
        }
        status, handoff = request("POST", "/api/v1/care-handoffs", body=handoff_payload, token=token_a)
        require(status == 201, f"handoff creation failed: {status} {handoff}")
        handoff_id = handoff["id"]
        require(handoff["created_by_caregiver_id"] == caregiver_id, "handoff creator attribution mismatch")
        require(handoff["outstanding_items"] == ["Confirm the appointment date"], "handoff outstanding items were not persisted")

        status, ack = request("POST", f"/api/v1/care-handoffs/{handoff_id}/acknowledge", token=token_a)
        require(status == 200 and ack["status"] == "acknowledged", f"handoff acknowledgement failed: {status} {ack}")
        require(ack["acknowledged_by_caregiver_id"] == caregiver_id and ack["acknowledged_at"], "acknowledgement attribution missing")

        # Create source evidence through the real capture API, then create a non-medication
        # action using a literal source quote so the reminder endpoint validates provenance.
        source_text = "Schedule a follow-up appointment with the clinic."
        status, capture = request(
            "POST", "/api/situation", token=token_a,
            body={
                "person_id": person_id,
                "caregiver_id": caregiver_id,
                "raw_input": source_text,
                "care_session_id": "coordination-e2e-source",
            },
        )
        require(status == 200, f"source capture failed: {status} {capture}")
        status, evidence_rows = request("GET", f"/api/v1/evidence/{person_id}", token=token_a)
        require(status == 200 and isinstance(evidence_rows, list), f"evidence lookup failed: {status} {evidence_rows}")
        evidence = next((item for item in evidence_rows if source_text in (item.get("source_text") or "")), None)
        require(evidence is not None, "captured source evidence was not retrievable for action provenance")

        status, action = request(
            "POST", "/api/v1/actions", token=token_a,
            body={
                "person_id": person_id,
                "source_evidence_id": evidence["id"],
                "source_passage": source_text,
                "source_document_type": "care_note",
                "source_authority": "caregiver",
                "extraction_confidence": 0.95,
                "actor_type": "caregiver",
                "subject_type": "patient",
                "action_type": "schedule",
                "normalized_action": "Schedule a follow-up appointment with the clinic",
                "original_text": source_text,
                "modality": "required",
                "status": "active",
                "is_explicit": True,
                "is_medication_action": False,
                "risk_tier": "low",
                "requires_confirmation": False,
                "has_condition": False,
                "is_recurring": False,
                "created_by_caregiver_id": caregiver_id,
            },
        )
        require(status == 200, f"source-grounded action creation failed: {status} {action}")
        action_id = action["id"]
        require(action["status"] == "active", f"action was not activated for reminder test: {action['status']}")

        # Exercise every read route consumed by the frontend dashboard against
        # the real PostgreSQL API before validating proactive item persistence.
        for dashboard_path in (
            f"/api/v1/caregivers/{person_id}",
            f"/api/v1/events/{person_id}",
            f"/api/v1/insights/{person_id}",
            f"/api/v1/observations/{person_id}",
            f"/api/v1/reasoning/{person_id}/summary",
            f"/api/v1/actions/{person_id}",
            f"/api/v1/appointments/{person_id}",
        ):
            dashboard_status, dashboard_payload = request("GET", dashboard_path, token=token_a)
            require(dashboard_status == 200, f"frontend dashboard API route failed ({dashboard_path}): {dashboard_status} {dashboard_payload}")

        # The same endpoint used by the frontend dashboard must persist a
        # source-linked proactive item and update today's snapshot idempotently.
        status, intelligence = request("GET", f"/api/v1/daily-intelligence/{person_id}", token=token_a)
        require(status == 200, f"daily intelligence endpoint failed: {status} {intelligence}")
        proactive = [item for item in intelligence.get("proactive_items", []) if item.get("source_id") == action_id]
        require(len(proactive) == 1, f"active action did not produce one source-linked proactive cue: {proactive}")
        cue = proactive[0]
        require(cue.get("title") and cue.get("message"), "proactive cue is missing its title or explanation")
        require(cue.get("source_evidence_ids") == [evidence["id"]], "proactive cue lost its source evidence reference")
        require(cue.get("time_provenance") == "no_deadline_recorded", "proactive cue misstated deadline provenance")

        status, intelligence_again = request("GET", f"/api/v1/daily-intelligence/{person_id}", token=token_a)
        require(status == 200, f"second daily intelligence read failed: {status} {intelligence_again}")
        require(intelligence_again["id"] == intelligence["id"], "daily intelligence endpoint created duplicate daily snapshots")
        second_action_cues = [item for item in intelligence_again.get("proactive_items", []) if item.get("source_id") == action_id]
        require(len(second_action_cues) == 1, "repeated dashboard read duplicated the same proactive cue")

        from app.core.database import SessionLocal
        from app.models.time_location import DailyIntelligence
        db = SessionLocal()
        try:
            saved_snapshots = db.query(DailyIntelligence).filter(DailyIntelligence.person_id == person_id).all()
            require(len(saved_snapshots) == 1, f"expected one persisted daily snapshot, got {len(saved_snapshots)}")
            require(any(row.id == intelligence["id"] and row.proactive_items for row in saved_snapshots), "proactive items were not persisted in PostgreSQL")
        finally:
            db.close()

        status, action_reminder = request(
            "POST", f"/api/v1/reminders/actions/{action_id}", token=token_a,
            body={
                "title": "Confirm follow-up appointment",
                "message": "Check the clinic appointment date and record the answer.",
                "scheduled_at": reminder_at.isoformat(),
                "timezone": "UTC",
                "recurrence": "once",
                "local_time": now.strftime("%H:%M"),
                "max_attempts": 3,
                "retry_delay_seconds": 30,
            },
        )
        require(status == 201, f"action reminder creation failed: {status} {action_reminder}")
        action_reminder_id = action_reminder["id"]
        require(action_reminder["action_id"] == action_id, "reminder/action linkage missing")

        status, reminders = request("GET", f"/api/v1/reminders/people/{person_id}", token=token_a)
        require(status == 200, f"reminder list failed: {status} {reminders}")
        handoff_reminder = next((item for item in reminders if item.get("handoff_id") == handoff_id), None)
        require(handoff_reminder is not None, "handoff did not create a durable linked reminder")
        require(any(item.get("id") == action_reminder_id for item in reminders), "action reminder was not returned by the scoped reminder list")

        # Closing a second handoff before its due reminder runs must suppress delivery.
        cancelled_payload = dict(handoff_payload)
        cancelled_payload.update({
            "title": "Cancelled handoff E2E",
            "summary": "This reminder must not be delivered after closure.",
        })
        status, cancelled_handoff = request("POST", "/api/v1/care-handoffs", body=cancelled_payload, token=token_a)
        require(status == 201, f"cancel-test handoff creation failed: {status} {cancelled_handoff}")
        cancelled_id = cancelled_handoff["id"]
        status, closed_cancel = request(
            "POST", f"/api/v1/care-handoffs/{cancelled_id}/close", token=token_a,
            body={"completion_report": "Closed before reminder delivery for scheduler cancellation test."},
        )
        require(status == 200 and closed_cancel["status"] == "closed", "pre-delivery handoff closure failed")

        from app.core.database import SessionLocal
        from app.services.reminder_scheduler import materialize_due_reminders, deliver_pending_receipts

        db = SessionLocal()
        try:
            created_receipts = materialize_due_reminders(db, now=now + timedelta(seconds=2))
            delivered_receipts = deliver_pending_receipts(db, now=now + timedelta(seconds=3))
            require(created_receipts >= 2, f"expected action and handoff receipts to materialize; got {created_receipts}")
            require(delivered_receipts >= 2, f"expected action and handoff reminders to deliver; got {delivered_receipts}")
        finally:
            db.close()

        for reminder_id in (action_reminder_id, handoff_reminder["id"]):
            status, receipts = request("GET", f"/api/v1/reminders/{reminder_id}/deliveries", token=token_a)
            require(status == 200 and len(receipts) == 1, f"expected one durable delivery receipt for {reminder_id}: {status} {receipts}")
            receipt = receipts[0]
            require(receipt["status"] == "delivered" and receipt["attempt_count"] == 1 and receipt["delivered_at"], "delivery receipt state/attempt count is wrong")
            status, attempts = request(
                "GET", f"/api/v1/reminders/{reminder_id}/deliveries/{receipt['id']}/attempts", token=token_a
            )
            require(status == 200 and len(attempts) == 1, f"delivery attempt history missing: {status} {attempts}")
            require(attempts[0]["channel"] == "in_app" and attempts[0]["outcome"] == "delivered", "delivery attempt was not a recorded in-app success")

        status, cancelled_reminders = request("GET", f"/api/v1/reminders/people/{person_id}", token=token_a)
        cancelled_reminder = next((item for item in cancelled_reminders if item.get("handoff_id") == cancelled_id), None)
        require(cancelled_reminder is not None and cancelled_reminder["enabled"] is False, "closing a handoff did not disable its pending reminder")
        status, cancelled_receipts = request("GET", f"/api/v1/reminders/{cancelled_reminder['id']}/deliveries", token=token_a)
        require(status == 200 and len(cancelled_receipts) == 0, "closed handoff reminder was delivered after closure")

        status, foreign_dashboard = request("GET", f"/api/v1/daily-intelligence/{person_id}", token=token_b)
        require(status == 403, f"cross-record proactive dashboard access was not denied: {status}")
        status, foreign_handoff = request("GET", f"/api/v1/care-handoffs/{handoff_id}", token=token_b)
        require(status in {403, 404}, f"cross-person handoff access was not denied: {status}")
        status, foreign_reminders = request("GET", f"/api/v1/reminders/{action_reminder_id}/deliveries", token=token_b)
        require(status in {403, 404}, f"cross-person reminder receipt access was not denied: {status}")

        status, closed = request(
            "POST", f"/api/v1/care-handoffs/{handoff_id}/close", token=token_a,
            body={"completion_report": "Clinic contacted; follow-up date confirmed and recorded."},
        )
        require(status == 200 and closed["status"] == "closed", f"handoff close failed: {status} {closed}")
        require(closed["completed_by_caregiver_id"] == caregiver_id and closed["completed_at"], "handoff completion attribution missing")
        status, events = request("GET", f"/api/v1/care-handoffs/{handoff_id}/events", token=token_a)
        require(status == 200, f"handoff event history failed: {status} {events}")
        event_types = [event["event_type"] for event in events]
        require(event_types == ["created", "acknowledged", "completion_reported", "closed"], f"unexpected handoff audit events: {event_types}")
        require(all(event.get("actor_caregiver_id") for event in events), "handoff event actor attribution missing")

        stop_server(server)
        server = start_server()
        status, persisted = request("GET", f"/api/v1/care-handoffs/{handoff_id}", token=token_a)
        require(status == 200 and persisted["status"] == "closed", "handoff did not persist after server restart")
        status, persisted_reminders = request("GET", f"/api/v1/reminders/people/{person_id}", token=token_a)
        require(status == 200 and any(item["id"] == action_reminder_id for item in persisted_reminders), "reminder did not persist after server restart")

        print("PASS: PostgreSQL HTTP handoff creation, acknowledgement, completion attribution,")
        print("      action and handoff reminders, durable delivery receipts/attempts, cancellation,")
        print("      cross-record access denial, audit history, and restart persistence.")
    finally:
        if server.poll() is None:
            stop_server(server)


if __name__ == "__main__":
    main()
