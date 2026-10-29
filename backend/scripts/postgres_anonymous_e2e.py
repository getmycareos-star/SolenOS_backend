"""Real HTTP + PostgreSQL smoke test for anonymous care continuity.

Runs only against the disposable PostgreSQL database supplied by CI. Starts and
restarts Uvicorn itself so persistence is verified across an application restart.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_URL = "http://127.0.0.1:8012"
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg2://")):
    raise SystemExit("Refusing PostgreSQL E2E smoke: DATABASE_URL must be PostgreSQL.")


def request(method: str, path: str, *, body=None, token: str | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            decoded = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            decoded = payload.decode("utf-8", "replace")
        return exc.code, decoded


def start_server():
    log_path = Path("/tmp/solenos-postgres-e2e-uvicorn.log")
    log = log_path.open("ab")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8012"],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    for _ in range(40):
        if process.poll() is not None:
            log.close()
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
            raise RuntimeError(f"Uvicorn exited early; see {log_path}\\n{detail}")
        try:
            status, ready = request("GET", "/ready")
        except Exception:
            time.sleep(0.5)
            continue
        if status == 200:
            require(ready.get("migration_state") == "current",
                    f"PostgreSQL readiness did not verify current Alembic head: {ready}")
            require(ready.get("migration_head"),
                    f"PostgreSQL readiness did not report its Alembic head: {ready}")
            require(ready.get("continuity_schema") == "verified",
                    f"PostgreSQL readiness did not verify migration-created continuity tables: {ready}")
            log.close()
            return process
        time.sleep(0.5)
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    log.close()
    detail = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
    raise RuntimeError(f"Uvicorn did not become ready; see {log_path}\\n{detail}")


def stop_server(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    if process.returncode not in (0, -15):
        raise RuntimeError(f"Uvicorn exited with code {process.returncode}")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    server = start_server()
    try:
        status, _ = request("GET", "/health")
        require(status == 200, f"/health expected 200, got {status}")

        status, first = request(
            "POST",
            "/api/v1/sessions/anonymous",
            body={"name": "PostgreSQL E2E person A", "caregiver_name": "PostgreSQL E2E caregiver A"},
        )
        require(status == 201, f"anonymous session creation expected 201, got {status}: {first}")
        token_a = first["access_token"]
        person_a = first["person"]["id"]
        caregiver_a = first["caregiver"]["id"]

        status, second = request(
            "POST",
            "/api/v1/sessions/anonymous",
            body={"name": "PostgreSQL E2E person B", "caregiver_name": "PostgreSQL E2E caregiver B"},
        )
        require(status == 201, f"second anonymous session creation expected 201, got {status}: {second}")
        token_b = second["access_token"]
        person_b = second["person"]["id"]

        # Companion idempotency is exercised against the real PostgreSQL schema
        # over HTTP without depending on Qwen availability: emergency-language
        # handling is deterministic and must still persist conversation-only turns.
        companion_turn_id = "e8b2c1ad-4d8f-4f5c-9a42-000000000001"
        companion_payload = {
            "person_id": person_a,
            "text": "Synthetic test only: she is unconscious and unresponsive.",
            "client_turn_id": companion_turn_id,
        }
        status, companion_first = request(
            "POST", "/api/v1/conversation/message", token=token_a, body=companion_payload,
        )
        require(status == 200, f"PostgreSQL Companion first turn failed: {status}: {companion_first}")
        require(companion_first.get("written_to_lcr") is False
                and companion_first.get("persistence") == "conversation_only",
                f"Companion emergency turn must remain conversation-only: {companion_first}")
        status, companion_replay = request(
            "POST", "/api/v1/conversation/message", token=token_a, body=companion_payload,
        )
        require(status == 200, f"PostgreSQL Companion retry replay failed: {status}: {companion_replay}")
        require(companion_replay.get("session_id") == companion_first.get("session_id")
                and companion_replay.get("reply") == companion_first.get("reply")
                and companion_replay.get("messages") == companion_first.get("messages"),
                f"Companion retry did not replay the exact committed turn: {companion_replay}")
        companion_session_id = companion_first["session_id"]

        status, changed_turn_reuse = request(
            "POST", "/api/v1/conversation/message", token=token_a,
            body={**companion_payload, "text": "Synthetic test only: a different message."},
        )
        require(status == 409,
                f"Companion must reject a turn key reused with changed text: {status}: {changed_turn_reuse}")
        status, cross_person_turn_reuse = request(
            "POST", "/api/v1/conversation/message", token=token_b,
            body={
                "person_id": person_b,
                "text": companion_payload["text"],
                "client_turn_id": companion_turn_id,
            },
        )
        require(status == 409 and companion_first.get("reply") not in json.dumps(cross_person_turn_reuse),
                f"Cross-person Companion retry key must not disclose the original reply: {status}: {cross_person_turn_reuse}")

        concurrent_turn_id = "e8b2c1ad-4d8f-4f5c-9a42-000000000002"
        concurrent_companion_payload = {
            "person_id": person_a,
            "text": "Synthetic concurrency test only: she is unconscious and unresponsive.",
            "client_turn_id": concurrent_turn_id,
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            companion_race = list(pool.map(
                lambda _: request(
                    "POST", "/api/v1/conversation/message",
                    token=token_a, body=concurrent_companion_payload,
                ),
                range(2),
            ))
        require(all(status == 200 for status, _ in companion_race),
                f"Concurrent same-key Companion submissions must both succeed by replay: {companion_race}")
        concurrent_sessions = {body.get("session_id") for _, body in companion_race}
        concurrent_replies = {body.get("reply") for _, body in companion_race}
        require(len(concurrent_sessions) == 1 and None not in concurrent_sessions
                and len(concurrent_replies) == 1,
                f"Concurrent Companion submissions did not converge to one committed turn: {companion_race}")
        concurrent_companion_session_id = next(iter(concurrent_sessions))

        # Professional-input evidence must resolve to a real source row in the
        # same PostgreSQL-backed care record; caregiver prose alone is not proof.
        status, source_record = request(
            "POST", "/api/v1/evidence", token=token_a, body={
                "person_id": person_a,
                "uploaded_by_caregiver_id": caregiver_a,
                "type": "document",
                "evidence_kind": "documented_record",
                "epistemic_status": "unverified",
                "source_text": "Synthetic CI-only source record for professional input verification.",
                "source_type": "ci_open_loop_fixture",
                "ingestion_key": "postgres-open-loop-professional-source-001",
            },
        )
        require(status in {200, 201} and source_record.get("id"),
                f"PostgreSQL source evidence creation failed: {status}: {source_record}")
        professional_source_id = source_record["id"]
        status, professional_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body={
                "loop_type": "professional_input",
                "title": "CI-only source-backed professional response",
                "description": "Synthetic CI fixture requiring a verified source record.",
                "idempotency_key": "postgres-open-loop-professional-input-001",
                "evidence": [],
            },
        )
        require(status == 201, f"Professional-input loop creation failed: {status}: {professional_loop}")
        professional_loop_id = professional_loop["id"]
        status, unsupported_professional_note = request(
            "POST", f"/api/v1/open-loops/{professional_loop_id}/responses", token=token_a,
            body={"text": "Caregiver says the professional called",
                  "evidence": [{"kind": "caregiver_recorded", "text": "A professional called"}]},
        )
        require(status == 422,
                f"Caregiver prose alone must not substantiate professional input: {status}: {unsupported_professional_note}")
        status, foreign_source_loop = request(
            "POST", "/api/v1/open-loops", token=token_b, body={
                "loop_type": "professional_input",
                "title": "CI-only foreign-source isolation",
                "description": "Synthetic test: source evidence from another person must be denied.",
                "idempotency_key": "postgres-open-loop-foreign-source-001",
                "evidence": [],
            },
        )
        require(status == 201, f"Foreign-source isolation loop creation failed: {status}: {foreign_source_loop}")
        status, foreign_source_attempt = request(
            "POST", f"/api/v1/open-loops/{foreign_source_loop['id']}/responses", token=token_b,
            body={"text": "Attempt to reference another person's source",
                  "evidence": [{"kind": "care_record_source", "evidence_id": professional_source_id}]},
        )
        require(status == 404,
                f"Cross-person source evidence reference must return 404: {status}: {foreign_source_attempt}")
        status, accepted_professional_response = request(
            "POST", f"/api/v1/open-loops/{professional_loop_id}/responses", token=token_a,
            body={"text": "Source-backed professional response recorded",
                  "evidence": [{"kind": "care_record_source", "evidence_id": professional_source_id}]},
        )
        require(status == 200,
                f"Source-backed professional response failed: {status}: {accepted_professional_response}")
        status, closed_professional_loop = request(
            "POST", f"/api/v1/open-loops/{professional_loop_id}/close", token=token_a,
            body={"text": "Reviewed against source record",
                  "evidence": [{"kind": "closure_review", "text": "Synthetic CI closure review"}]},
        )
        require(status == 200 and closed_professional_loop.get("loop", {}).get("status") == "closed",
                f"Source-backed professional loop did not close correctly: {status}: {closed_professional_loop}")

        # Frontend-aligned complete workflow against the real PostgreSQL-backed API:
        # create -> response/action -> outcome -> explicit verification -> close -> history.
        workflow_source = [{"kind": "care_record_source", "evidence_id": professional_source_id}]
        status, workflow_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body={
                "loop_type": "follow_up",
                "title": "CI-only end-to-end follow-up workflow",
                "description": "Synthetic API workflow covering every persisted handoff stage.",
                "idempotency_key": "postgres-open-loop-full-workflow-001",
                "evidence": [{"kind": "ci_fixture", "text": "Synthetic workflow only."}],
                "verification_rule": "The linked synthetic source record is present and was reviewed.",
            },
        )
        require(status == 201, f"Full Open Loop workflow creation failed: {status}: {workflow_loop}")
        workflow_loop_id = workflow_loop["id"]
        status, workflow_response = request(
            "POST", f"/api/v1/open-loops/{workflow_loop_id}/responses", token=token_a,
            body={"text": "Synthetic response recorded", "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-full-response-001"},
        )
        require(status == 200, f"Full workflow response recording failed: {status}: {workflow_response}")
        status, workflow_action = request(
            "POST", f"/api/v1/open-loops/{workflow_loop_id}/actions", token=token_a,
            body={"text": "Synthetic follow-up action recorded", "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-full-action-001"},
        )
        require(status == 200, f"Full workflow action recording failed: {status}: {workflow_action}")
        status, workflow_outcome = request(
            "POST", f"/api/v1/open-loops/{workflow_loop_id}/outcomes", token=token_a,
            body={"text": "Synthetic outcome reported", "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-full-outcome-001"},
        )
        require(status == 200, f"Full workflow outcome recording failed: {status}: {workflow_outcome}")
        status, workflow_verified = request(
            "POST", f"/api/v1/open-loops/{workflow_loop_id}/verify-outcome", token=token_a,
            body={"text": "Synthetic outcome reviewed against the linked record",
                  "evidence": workflow_source,
                  "verification_rule": "The linked synthetic source record is present and was reviewed.",
                  "idempotency_key": "postgres-open-loop-full-verify-001"},
        )
        require(status == 200 and workflow_verified.get("outcome_status") == "outcome_verified",
                f"Full workflow outcome verification failed: {status}: {workflow_verified}")
        status, workflow_closed = request(
            "POST", f"/api/v1/open-loops/{workflow_loop_id}/close", token=token_a,
            body={"text": "Synthetic closure after recorded verification",
                  "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-full-close-001"},
        )
        require(status == 200 and workflow_closed.get("loop", {}).get("status") == "closed",
                f"Full workflow closure failed: {status}: {workflow_closed}")
        status, workflow_history = request(
            "GET", f"/api/v1/open-loops/{workflow_loop_id}/history?limit=100", token=token_a,
        )
        workflow_event_types = {event.get("event_type") for event in workflow_history}
        require(status == 200 and {
            "created", "response_recorded", "action_recorded", "outcome_reported",
            "outcome_verified", "closure_verified",
        }.issubset(workflow_event_types),
                f"Full workflow audit history is incomplete: {status}: {workflow_history}")

        # Conflicting terminal transitions must serialize: exactly one of close
        # or cancel may win, and the loser must receive a conflict rather than
        # silently replacing the first terminal decision.
        status, conflict_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body={
                "loop_type": "follow_up",
                "title": "CI-only competing terminal transitions",
                "description": "Synthetic race between verified closure and cancellation.",
                "idempotency_key": "postgres-open-loop-terminal-race-001",
                "evidence": [{"kind": "ci_fixture", "text": "Synthetic lifecycle race only."}],
                "verification_rule": "The linked synthetic source record is present and was reviewed.",
            },
        )
        require(status == 201, f"Terminal race loop creation failed: {status}: {conflict_loop}")
        conflict_loop_id = conflict_loop["id"]
        for path, payload, label in [
            ("outcomes", {"text": "Synthetic outcome", "evidence": workflow_source}, "outcome"),
            ("verify-outcome", {"text": "Synthetic verification", "evidence": workflow_source,
                                "verification_rule": "The linked synthetic source record is present and was reviewed."}, "verification"),
        ]:
            status, result = request(
                "POST", f"/api/v1/open-loops/{conflict_loop_id}/{path}", token=token_a,
                body={**payload, "idempotency_key": f"postgres-open-loop-terminal-race-{label}-001"},
            )
            require(status == 200, f"Terminal race preparation {label} failed: {status}: {result}")
        terminal_actions = [
            ("close", {"text": "Closure won if accepted", "evidence": workflow_source,
                       "idempotency_key": "postgres-open-loop-terminal-race-close-001"}),
            ("cancel", {"text": "Cancellation won if accepted", "evidence": workflow_source,
                        "idempotency_key": "postgres-open-loop-terminal-race-cancel-001"}),
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            terminal_results = list(pool.map(
                lambda item: request(
                    "POST", f"/api/v1/open-loops/{conflict_loop_id}/{item[0]}",
                    token=token_a, body=item[1],
                ),
                terminal_actions,
            ))
        terminal_statuses = [status for status, _ in terminal_results]
        require(sorted(terminal_statuses) == [200, 409],
                f"Conflicting close/cancel transitions must have one winner and one 409 conflict: {terminal_results}")
        status, terminal_final = request(
            "GET", f"/api/v1/open-loops/{conflict_loop_id}", token=token_a,
        )
        require(status == 200 and terminal_final.get("loop", {}).get("status") in {"closed", "cancelled"},
                f"Terminal race did not leave one durable terminal state: {status}: {terminal_final}")
        status, terminal_history = request(
            "GET", f"/api/v1/open-loops/{conflict_loop_id}/history?limit=100", token=token_a,
        )
        terminal_events = [event.get("event_type") for event in terminal_history
                           if event.get("event_type") in {"closure_verified", "cancelled"}]
        require(status == 200 and len(terminal_events) == 1,
                f"Terminal race must persist exactly one terminal audit event: {status}: {terminal_history}")

        # Contradiction reconciliation and verification are separate explicit
        # steps; closing without both must not be inferred from narrative alone.
        status, contradiction_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body={
                "loop_type": "contradiction",
                "title": "CI-only conflicting reports",
                "description": "Synthetic conflict requiring explicit reconciliation.",
                "idempotency_key": "postgres-open-loop-reconciliation-001",
                "evidence": [{"kind": "ci_fixture", "text": "Synthetic contradiction workflow only."}],
            },
        )
        require(status == 201, f"Contradiction loop creation failed: {status}: {contradiction_loop}")
        contradiction_id = contradiction_loop["id"]
        status, premature_close = request(
            "POST", f"/api/v1/open-loops/{contradiction_id}/close", token=token_a,
            body={"text": "Premature close attempt", "evidence": workflow_source},
        )
        require(status == 409, f"Unreconciled contradiction must not close: {status}: {premature_close}")
        status, reconciliation = request(
            "POST", f"/api/v1/open-loops/{contradiction_id}/reconcile", token=token_a,
            body={"text": "Synthetic explicit reconciliation review; disagreement is not treated as semantic proof.",
                  "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-reconciliation-event-001"},
        )
        require(status == 200, f"Contradiction reconciliation failed: {status}: {reconciliation}")
        status, contradiction_verified = request(
            "POST", f"/api/v1/open-loops/{contradiction_id}/verify-outcome", token=token_a,
            body={"text": "Reconciliation reviewed against the linked source",
                  "evidence": workflow_source,
                  "verification_rule": "The linked synthetic source record is present and was reviewed.",
                  "idempotency_key": "postgres-open-loop-reconciliation-verify-001"},
        )
        require(status == 200 and contradiction_verified.get("outcome_status") == "outcome_verified",
                f"Contradiction verification failed: {status}: {contradiction_verified}")
        status, contradiction_closed = request(
            "POST", f"/api/v1/open-loops/{contradiction_id}/close", token=token_a,
            body={"text": "Synthetic closure after reconciliation and verification",
                  "evidence": workflow_source,
                  "idempotency_key": "postgres-open-loop-reconciliation-close-001"},
        )
        require(status == 200 and contradiction_closed.get("loop", {}).get("status") == "closed",
                f"Reconciled contradiction did not close: {status}: {contradiction_closed}")
        status, contradiction_history = request(
            "GET", f"/api/v1/open-loops/{contradiction_id}/history?limit=100", token=token_a,
        )
        contradiction_event_types = {event.get("event_type") for event in contradiction_history}
        require(status == 200 and {
            "created", "contradiction_reconciled", "outcome_verified", "closure_verified",
        }.issubset(contradiction_event_types),
                f"Contradiction audit history is incomplete: {status}: {contradiction_history}")

        # Real PostgreSQL observation-coverage persistence, authorization, and
        # explicit coverage assessment. This never uses the deployed Railway API.
        coverage_start = datetime.now(timezone.utc) - timedelta(days=14)
        coverage_end = datetime.now(timezone.utc) - timedelta(minutes=1)
        coverage_body = {
            "person_id": person_a,
            "subject_type": "mobility",
            "window_start": coverage_start.isoformat(),
            "window_end": coverage_end.isoformat(),
            "coverage_status": "covered",
            "source_evidence_ids": [],
            "rationale": "CI fixture explicitly declares the mobility domain observed throughout this interval.",
        }
        status, saved_coverage = request(
            "POST", "/api/v1/change/coverage", token=token_a, body=coverage_body,
        )
        require(status == 201, f"coverage creation expected 201, got {status}: {saved_coverage}")
        coverage_query = urlencode({
            "subject_type": "mobility",
            "window_start": coverage_start.isoformat(),
            "window_end": coverage_end.isoformat(),
        })
        status, assessed_coverage = request(
            "GET", f"/api/v1/change/coverage/{person_a}?{coverage_query}", token=token_a,
        )
        require(status == 200, f"coverage assessment expected 200, got {status}: {assessed_coverage}")
        require(
            assessed_coverage.get("coverage_status") == "covered"
            and assessed_coverage.get("coverage_fraction") == 1.0
            and any(row.get("id") == saved_coverage.get("id") for row in assessed_coverage.get("records", [])),
            f"coverage assessment did not return persisted complete interval: {assessed_coverage}",
        )
        status, cross_person_coverage = request(
            "GET", f"/api/v1/change/coverage/{person_a}?{coverage_query}", token=token_b,
        )
        require(
            status == 403,
            f"cross-person observation-coverage read must return 403, got {status}: {cross_person_coverage}",
        )

        # Real HTTP + PostgreSQL change/pattern reconciliation after late-arriving
        # source events. These are synthetic CI records, never production care data.
        status, pattern_session = request(
            "POST",
            "/api/v1/sessions/anonymous",
            body={"name": "PostgreSQL pattern E2E person C", "caregiver_name": "PostgreSQL pattern E2E caregiver C"},
        )
        require(status == 201, f"pattern E2E session creation expected 201, got {status}: {pattern_session}")
        token_c = pattern_session["access_token"]
        person_c = pattern_session["person"]["id"]
        caregiver_c = pattern_session["caregiver"]["id"]
        pattern_event_ids = []
        for suffix, days_ago in (("old", 20), ("middle", 10), ("recent", 2)):
            event_time = datetime.now(timezone.utc) - timedelta(days=days_ago)
            status, event_record = request(
                "POST",
                "/api/v1/events",
                token=token_c,
                body={
                    "person_id": person_c,
                    "event_type": "fall",
                    "status": "recorded",
                    "occurred_at": event_time.isoformat(),
                    "title": f"CI-only fall event {suffix}",
                    "description": "Synthetic integration fixture; not a real care report.",
                    "created_by_caregiver_id": caregiver_c,
                    "time_provenance": "ci_fixture",
                    "tags": ["ci_fixture", "change_intelligence"],
                },
            )
            require(status == 200, f"care event create expected 200, got {status}: {event_record}")
            pattern_event_ids.append(event_record["id"])

        status, persisted_patterns = request(
            "GET", f"/api/v1/change/patterns/{person_c}?subject_type=fall", token=token_c,
        )
        require(status == 200, f"pattern list expected 200, got {status}: {persisted_patterns}")
        recurrence_patterns = [
            pattern for pattern in persisted_patterns
            if pattern.get("pattern_type") == "recurrence"
            and pattern.get("is_active") is True
            and set(pattern.get("event_ids") or []).issuperset(pattern_event_ids)
        ]
        require(
            recurrence_patterns,
            "newly recorded source events did not produce a persisted source-linked recurrence pattern",
        )
        persisted_recurrence_id = recurrence_patterns[0]["id"]
        status, cross_person_patterns = request(
            "GET", f"/api/v1/change/patterns/{person_c}?subject_type=fall", token=token_b,
        )
        require(
            status == 403,
            f"cross-person pattern access must return 403, got {status}: {cross_person_patterns}",
        )

        # Real PostgreSQL uncertainty API persistence and record-isolation checks.
        status, created_uncertainty = request("POST", "/api/v1/uncertainty/assessments", token=token_a, body={
            "person_id": person_a, "subject_type": "medication",
            "subject_id": "postgres-e2e-unknown-medication", "field": "current_status",
            "epistemic_state": "unknown", "gap_reason": "not_documented", "known_value": None,
        })
        require(status == 200, f"uncertainty create expected 200, got {status}: {created_uncertainty}")
        require(created_uncertainty.get("epistemic_state") == "unknown"
                and created_uncertainty.get("confidence") is None,
                f"unknown state/confidence contract failed: {created_uncertainty}")
        status, persisted_uncertainty = request(
            "GET", f"/api/v1/uncertainty/assessments/{person_a}?subject_type=medication&subject_id=postgres-e2e-unknown-medication",
            token=token_a,
        )
        require(status == 200 and any(
            item.get("field") == "current_status" and item.get("epistemic_state") == "unknown"
            and item.get("confidence") is None for item in persisted_uncertainty
        ), f"uncertainty assessment did not persist: {status}: {persisted_uncertainty}")
        status, uncertainty_view = request(
            "GET", f"/api/v1/uncertainty/view/{person_a}/medication/postgres-e2e-unknown-medication",
            token=token_a,
        )
        require(status == 200 and uncertainty_view.get("epistemic_state") == "unknown"
                and uncertainty_view.get("known_value") is None,
                f"unknown epistemic view contract failed: {status}: {uncertainty_view}")
        status, cross_uncertainty = request(
            "GET", f"/api/v1/uncertainty/assessments/{person_a}", token=token_b,
        )
        require(status == 403,
                f"cross-person uncertainty access must return 403, got {status}: {cross_uncertainty}")

        observation = "PostgreSQL end-to-end durable observation unique to person A"
        status, captured = request(
            "POST",
            "/api/situation",
            token=token_a,
            body={
                "person_id": person_a,
                "caregiver_id": caregiver_a,
                "raw_input": observation,
                "care_session_id": "postgres-e2e-interaction-a",
            },
        )
        require(status == 200, f"care capture expected 200, got {status}: {captured}")
        require(captured.get("person_id") == person_a, "capture response person scope mismatch")

        status, hydration = request("GET", "/api/situation", token=token_a)
        require(status == 200, f"hydration expected 200, got {status}: {hydration}")
        raw_observations = [
            item.get("raw_text")
            for item in (hydration.get("active_care_situation") or {}).get("observations", [])
        ]
        require(observation in raw_observations, "capture did not appear in persisted hydration")

        # Authenticated UPDATE must persist in PostgreSQL and be visible on a fresh read.
        updated_name = "PostgreSQL E2E person A updated"
        status, updated = request(
            "PATCH",
            "/api/situation",
            token=token_a,
            body={
                "person_id": person_a,
                "caregiver_id": caregiver_a,
                "display_name": updated_name,
            },
        )
        require(status == 200, f"care-record update expected 200, got {status}: {updated}")
        require(
            updated.get("care_recipient_display_name") == updated_name,
            f"update response did not confirm the new display name: {updated}",
        )
        status, reread = request("GET", "/api/situation", token=token_a)
        require(status == 200, f"read after update expected 200, got {status}: {reread}")
        require(
            reread.get("care_recipient_display_name") == updated_name,
            "authenticated update was not persisted to PostgreSQL",
        )

        # Account login on a fresh client/session must recover the same care record.
        # This models a second device without reusing browser localStorage.
        account_email = "postgres-e2e-continuity@example.test"
        account_password = "CI-only-strong-passphrase-2026!"
        status, account_created = request(
            "POST",
            "/api/v1/accounts",
            token=token_a,
            body={"email": account_email, "password": account_password},
        )
        require(status == 201, f"account linking expected 201, got {status}: {account_created}")
        status, second_device = request(
            "POST",
            "/api/v1/accounts/login",
            body={"email": account_email, "password": account_password},
        )
        require(status == 200, f"second-device account login expected 200, got {status}: {second_device}")
        require(second_device.get("person_id") == person_a,
                "second-device login did not restore the same care-recipient identity")
        require(second_device.get("caregiver_id") == caregiver_a,
                "second-device login did not restore the same caregiver identity")
        token_a = second_device["access_token"]
        status, second_device_read = request("GET", "/api/situation", token=token_a)
        require(status == 200, f"second-device read expected 200, got {status}: {second_device_read}")
        require(second_device_read.get("care_recipient_display_name") == updated_name,
                "second-device login did not retrieve the persisted updated care record")

        status, _ = request("GET", f"/api/v1/evidence/{person_a}", token=token_b)
        require(status == 403, f"cross-care-record evidence access must return 403, got {status}")
        status, _ = request("GET", f"/api/v1/evidence/{person_a}")
        require(status == 401, f"unauthenticated evidence access must return 401, got {status}")

        status, recovery_session = request(
            "POST",
            "/api/v1/sessions/anonymous",
            body={"name": "PostgreSQL E2E recovery person", "caregiver_name": "PostgreSQL E2E recovery caregiver"},
        )
        require(status == 201, f"recovery session creation expected 201, got {status}: {recovery_session}")
        recovery_token = recovery_session["access_token"]
        status, revoked = request("POST", "/api/v1/sessions/revoke", body={}, token=recovery_token)
        require(status == 200 and revoked.get("revoked") is True,
                f"session revocation expected success, got {status}: {revoked}")
        status, _ = request("GET", f"/api/v1/evidence/{recovery_session['person']['id']}", token=recovery_token)
        require(status == 401, f"revoked token must be rejected, got {status}")
        # Revoking one capability must not break another caregiver's valid session.
        status, _ = request("GET", f"/api/v1/evidence/{person_a}", token=token_a)
        require(status == 200, f"valid independent session failed after revocation, got {status}")

        status, cross_write = request(
            "POST",
            "/api/situation",
            token=token_b,
            body={
                "person_id": person_a,
                "caregiver_id": caregiver_a,
                "raw_input": "This cross-recipient write must be rejected",
                "care_session_id": "postgres-e2e-cross-write",
            },
        )
        require(status == 403, f"cross-care-record write must return 403, got {status}: {cross_write}")

        token_before_refresh = token_a
        status, refreshed = request("POST", "/api/v1/sessions/refresh", token=token_before_refresh)
        require(status == 200, f"token refresh expected 200, got {status}: {refreshed}")
        token_a = refreshed["access_token"]
        status, _ = request("GET", f"/api/v1/evidence/{person_a}", token=token_before_refresh)
        require(status == 401, f"rotated token should be rejected, got {status}")
        status, _ = request("GET", f"/api/v1/evidence/{person_a}", token=token_a)
        require(status == 200, f"new token should retain record access, got {status}")

        # Open Loop Engine: real PostgreSQL persistence, idempotent retry, history,
        # cross-person isolation, and concurrent append integrity. All fixtures are synthetic.
        open_loop_payload = {
            "loop_type": "awaiting_response",
            "title": "CI-only clinic callback",
            "description": "Synthetic test: callback has not yet been confirmed.",
            "request_text": "Please confirm the appointment date.",
            "recipient_label": "CI-only clinic",
            "idempotency_key": "postgres-open-loop-create-retry-001",
            "evidence": [{"kind": "ci_fixture", "text": "Synthetic integration test only."}],
        }
        status, open_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body=open_loop_payload,
        )
        require(status == 201, f"Open Loop creation expected 201, got {status}: {open_loop}")
        open_loop_id = open_loop["id"]
        status, open_loop_retry = request(
            "POST", "/api/v1/open-loops", token=token_a, body=open_loop_payload,
        )
        require(status == 201 and open_loop_retry.get("id") == open_loop_id,
                f"Open Loop idempotent create retry created a duplicate or failed: {status}: {open_loop_retry}")
        changed_retry_payload = {**open_loop_payload, "title": "Different title with the same idempotency key"}
        status, changed_retry = request(
            "POST", "/api/v1/open-loops", token=token_a, body=changed_retry_payload,
        )
        require(status == 409,
                f"Reusing a create idempotency key with a different core payload must return 409: {status}: {changed_retry}")
        # Race two identical create requests. A unique database constraint plus
        # retry recovery must converge on one durable loop instead of returning 500.
        concurrent_create_payload = {
            "loop_type": "awaiting_response",
            "title": "CI-only concurrent idempotency callback",
            "description": "Synthetic race test for duplicate create retries.",
            "request_text": "Confirm the synthetic callback.",
            "recipient_label": "CI-only test recipient",
            "idempotency_key": "postgres-open-loop-concurrent-create-001",
            "evidence": [{"kind": "ci_fixture", "text": "Concurrent idempotency race only."}],
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_creates = list(pool.map(
                lambda _: request("POST", "/api/v1/open-loops", token=token_a, body=concurrent_create_payload),
                range(2),
            ))
        require(all(status == 201 for status, _ in concurrent_creates),
                f"Concurrent duplicate Open Loop creates must both resolve successfully: {concurrent_creates}")
        concurrent_ids = {payload.get("id") for _, payload in concurrent_creates}
        require(len(concurrent_ids) == 1 and None not in concurrent_ids,
                f"Concurrent duplicate creates did not converge to one loop: {concurrent_creates}")
        concurrent_loop_id = next(iter(concurrent_ids))
        status, open_loop_list = request("GET", "/api/v1/open-loops", token=token_a)
        require(status == 200 and sum(1 for item in open_loop_list.get("items", [])
                                      if item.get("id") == open_loop_id) == 1
                and sum(1 for item in open_loop_list.get("items", [])
                        if item.get("id") == concurrent_loop_id) == 1,
                f"Open Loop idempotency created duplicate list records: {status}: {open_loop_list}")
        status, cross_person_loop = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_b,
        )
        require(status == 404,
                f"Cross-person Open Loop read must return 404, got {status}: {cross_person_loop}")

        # Replaying the same mutation key must not duplicate an action event or evidence.
        retry_action = {
            "text": "CI-only action with stable retry key",
            "evidence": [{"kind": "ci_fixture", "text": "same request retried"}],
            "idempotency_key": "postgres-open-loop-action-retry-001",
        }
        first_action_status, _ = request(
            "POST", f"/api/v1/open-loops/{open_loop_id}/actions",
            token=token_a, body=retry_action,
        )
        retry_action_status, _ = request(
            "POST", f"/api/v1/open-loops/{open_loop_id}/actions",
            token=token_a, body=retry_action,
        )
        require(first_action_status == 200 and retry_action_status == 200,
                f"Same-key Open Loop action retry failed: {first_action_status}, {retry_action_status}")
        status, after_action_retry = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a,
        )
        retry_actions = [item for item in after_action_retry.get("loop", {}).get("action_evidence", [])
                         if item.get("text") == retry_action["text"]]
        require(status == 200 and len(retry_actions) == 1,
                f"Same-key action retry duplicated evidence: {retry_actions}")

        # Two simultaneous retries with the same mutation idempotency key
        # must converge to one event/evidence append (not one success plus a 500).
        concurrent_retry_payload = {
            "text": "CI-only concurrent retry action",
            "evidence": [{"kind": "ci_fixture", "text": "concurrent same-key retry"}],
            "idempotency_key": "postgres-open-loop-concurrent-action-retry-001",
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_action_retries = list(pool.map(
                lambda _: request(
                    "POST", f"/api/v1/open-loops/{open_loop_id}/actions",
                    token=token_a, body=concurrent_retry_payload,
                ),
                range(2),
            ))
        require(all(status == 200 for status, _ in concurrent_action_retries),
                f"Concurrent same-key action retries must both return successfully: {concurrent_action_retries}")
        status, after_concurrent_retry = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a,
        )
        same_key_actions = [
            item for item in after_concurrent_retry.get("loop", {}).get("action_evidence", [])
            if item.get("text") == concurrent_retry_payload["text"]
        ]
        require(status == 200 and len(same_key_actions) == 1,
                f"Concurrent same-key retries duplicated action evidence: {same_key_actions}")

        # Concurrent append attempts must not silently overwrite one another.
        # This is intentionally a strict invariant; if it fails, the database-backed
        # test should remain red rather than claiming concurrency safety.
        action_payloads = [
            {"text": "CI-only action A", "evidence": [{"kind": "ci_fixture", "text": "action-A"}]},
            {"text": "CI-only action B", "evidence": [{"kind": "ci_fixture", "text": "action-B"}]},
        ]
        def append_open_loop_action(payload):
            return request(
                "POST", f"/api/v1/open-loops/{open_loop_id}/actions",
                token=token_a, body=payload,
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            action_results = list(pool.map(append_open_loop_action, action_payloads))
        require(all(status == 200 for status, _ in action_results),
                f"Concurrent Open Loop action appends failed: {action_results}")
        status, open_loop_after_actions = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a,
        )
        require(status == 200, f"Open Loop read after concurrent actions failed: {status}: {open_loop_after_actions}")
        saved_actions = open_loop_after_actions["loop"].get("action_evidence", [])
        require(len(saved_actions) == 4 and
                {item.get("text") for item in saved_actions} >= {
                    "CI-only action with stable retry key",
                    "CI-only concurrent retry action",
                    "CI-only action A",
                    "CI-only action B",
                },
                f"Concurrent Open Loop updates lost an append or idempotent retry duplicated one: {saved_actions}")

        # Retrying a mutation with the same idempotency key must return the
        # committed result and must not duplicate evidence or audit history.
        # Reuse the exact original request and key; changing the payload while
        # reusing the key must be rejected (asserted above).
        retry_payload = dict(retry_action)
        def retry_action(_):
            return request(
                "POST", f"/api/v1/open-loops/{open_loop_id}/actions",
                token=token_a, body=retry_payload,
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            retry_results = list(pool.map(retry_action, (1, 2)))
        require(all(status == 200 for status, _ in retry_results),
                f"Concurrent idempotent action retries failed: {retry_results}")
        status, loop_after_retry = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a,
        )
        require(status == 200, f"Open Loop read after idempotent retry failed: {status}: {loop_after_retry}")
        retry_evidence_count = sum(
            1 for item in loop_after_retry["loop"].get("action_evidence", [])
            if item.get("text") == retry_payload["text"]
        )
        require(retry_evidence_count == 1,
                f"Idempotent action retry duplicated evidence: {loop_after_retry['loop'].get('action_evidence')}")
        status, history_after_retry = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}/history", token=token_a,
        )
        retry_event_count = sum(
            1 for event in history_after_retry
            if event.get("event_type") == "action_recorded"
            and any(item.get("text") == "same request retried" for item in event.get("evidence", []))
        )
        require(status == 200 and retry_event_count == 1,
                f"Idempotent action retry duplicated audit events: {status}: {history_after_retry}")
        status, conflicting_key_reuse = request(
            "POST", f"/api/v1/open-loops/{open_loop_id}/responses", token=token_a,
            body={"text": "Wrong event for same key", "evidence": [{"kind": "ci_fixture", "text": "wrong event"}],
                  "idempotency_key": retry_payload["idempotency_key"]},
        )
        require(status == 409,
                f"Reusing an idempotency key for a different event must return 409: {status}: {conflicting_key_reuse}")
        status, recorded_response = request(
            "POST", f"/api/v1/open-loops/{open_loop_id}/responses", token=token_a,
            body={"text": "CI-only response received", "evidence": [{"kind": "ci_fixture", "text": "response"}]},
        )
        require(status == 200, f"Open Loop response event expected 200, got {status}: {recorded_response}")
        status, open_loop_history = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}/history", token=token_a,
        )
        require(status == 200 and any(
            event.get("event_type") == "response_recorded" for event in open_loop_history
        ), f"Open Loop response audit event missing: {status}: {open_loop_history}")

        # Conflicting terminal transitions must serialize. Exactly one of close
        # and cancel may win; the loser must receive 409 rather than overwrite it.
        status, lifecycle_loop = request(
            "POST", "/api/v1/open-loops", token=token_a, body={
                "loop_type": "awaiting_response",
                "title": "CI-only terminal transition race",
                "description": "Synthetic concurrency test for close versus cancel.",
                "request_text": "Confirm the synthetic outcome before closing.",
                "recipient_label": "CI-only test recipient",
                "idempotency_key": "postgres-open-loop-terminal-race-create",
                "evidence": [{"kind": "ci_fixture", "text": "Terminal transition race fixture."}],
            },
        )
        require(status == 201, f"Terminal race loop creation failed: {status}: {lifecycle_loop}")
        lifecycle_loop_id = lifecycle_loop["id"]
        status, _ = request(
            "POST", f"/api/v1/open-loops/{lifecycle_loop_id}/responses", token=token_a,
            body={"text": "CI-only response before terminal race",
                  "evidence": [{"kind": "ci_fixture", "text": "Response recorded before competing transitions."}],
                  "idempotency_key": "postgres-open-loop-terminal-race-response"},
        )
        require(status == 200, f"Terminal race prerequisite response failed: {status}")
        close_payload = {
            "text": "CI-only close attempt",
            "evidence": [{"kind": "ci_fixture", "text": "Close attempt evidence."}],
            "idempotency_key": "postgres-open-loop-terminal-race-close",
        }
        cancel_payload = {
            "text": "CI-only cancel attempt",
            "evidence": [{"kind": "ci_fixture", "text": "Cancel attempt evidence."}],
            "idempotency_key": "postgres-open-loop-terminal-race-cancel",
        }
        def terminal_transition(item):
            endpoint, payload = item
            return request(
                "POST", f"/api/v1/open-loops/{lifecycle_loop_id}/{endpoint}",
                token=token_a, body=payload,
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            terminal_results = list(pool.map(
                terminal_transition, [("close", close_payload), ("cancel", cancel_payload)],
            ))
        terminal_statuses = sorted(status for status, _ in terminal_results)
        require(terminal_statuses == [200, 409],
                f"Concurrent close/cancel must have exactly one winner and one conflict: {terminal_results}")
        status, lifecycle_after_race = request(
            "GET", f"/api/v1/open-loops/{lifecycle_loop_id}", token=token_a,
        )
        final_status = lifecycle_after_race.get("loop", {}).get("status")
        require(status == 200 and final_status in {"closed", "cancelled"},
                f"Concurrent terminal transitions left an invalid state: {status}: {lifecycle_after_race}")
        status, lifecycle_history = request(
            "GET", f"/api/v1/open-loops/{lifecycle_loop_id}/history", token=token_a,
        )
        terminal_events = [
            event.get("event_type") for event in lifecycle_history
            if event.get("event_type") in {"closure_verified", "cancelled"}
        ]
        require(status == 200 and len(terminal_events) == 1,
                f"Conflicting terminal transitions must produce exactly one terminal audit event: {status}: {lifecycle_history}")

        stop_server(server)
        server = start_server()
        status, professional_after_restart = request(
            "GET", f"/api/v1/open-loops/{professional_loop_id}", token=token_a,
        )
        persisted_professional = professional_after_restart.get("loop", {}) if status == 200 else {}
        require(status == 200 and persisted_professional.get("status") == "closed"
                and any(
                    isinstance(entry, dict) and any(
                        isinstance(source, dict) and source.get("evidence_id") == professional_source_id
                        for source in (entry.get("evidence") or [])
                    )
                    for entry in persisted_professional.get("response_evidence", [])
                ),
                f"Source-backed professional response/closure did not persist across restart: {status}: {professional_after_restart}")

        status, open_loop_after_restart = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a,
        )
        require(status == 200, f"Open Loop read after Uvicorn restart expected 200, got {status}: {open_loop_after_restart}")
        persisted_loop = open_loop_after_restart.get("loop", {})
        require(persisted_loop.get("id") == open_loop_id
                and persisted_loop.get("status") == "open"
                and len(persisted_loop.get("action_evidence", [])) == 4
                and {item.get("text") for item in persisted_loop.get("action_evidence", [])} >= {
                    "CI-only action with stable retry key",
                    "CI-only concurrent retry action",
                    "CI-only action A",
                    "CI-only action B",
                }
                and len(persisted_loop.get("response_evidence", [])) == 1,
                f"Open Loop lifecycle/evidence did not persist across Uvicorn restart: {persisted_loop}")
        status, open_loop_history_after_restart = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}/history", token=token_a,
        )
        require(status == 200 and any(
            event.get("event_type") == "response_recorded" for event in open_loop_history_after_restart
        ), f"Open Loop audit history did not persist across restart: {status}: {open_loop_history_after_restart}")
        # Race two mutually exclusive terminal transitions. Exactly one may
        # win; the second must observe terminal state and conflict, not overwrite it.
        lifecycle_payloads = [
            ("POST", f"/api/v1/open-loops/{open_loop_id}/close",
             {"text": "CI-only closure", "evidence": [{"kind": "ci_fixture", "text": "closure race"}]}),
            ("POST", f"/api/v1/open-loops/{open_loop_id}/cancel",
             {"text": "CI-only cancellation", "evidence": [{"kind": "ci_fixture", "text": "cancellation race"}]}),
        ]
        def apply_lifecycle(item):
            method, path, body = item
            return request(method, path, token=token_a, body=body)
        with ThreadPoolExecutor(max_workers=2) as pool:
            lifecycle_results = list(pool.map(apply_lifecycle, lifecycle_payloads))
        require(sorted(status for status, _ in lifecycle_results) == [200, 409],
                f"Conflicting close/cancel updates must serialize to one winner: {lifecycle_results}")
        status, terminal_loop = request("GET", f"/api/v1/open-loops/{open_loop_id}", token=token_a)
        require(status == 200 and terminal_loop.get("loop", {}).get("status") in {"closed", "cancelled"},
                f"Open Loop did not persist a valid terminal state after lifecycle race: {status}: {terminal_loop}")
        status, reopened_loop = request(
            "POST", f"/api/v1/open-loops/{open_loop_id}/reopen", token=token_a,
            body={"text": "CI-only reopening after conflict test", "evidence": [{"kind": "ci_fixture", "text": "reopen"}]},
        )
        require(status == 200 and reopened_loop.get("status") == "open",
                f"Open Loop could not be reopened after terminal race: {status}: {reopened_loop}")
        status, lifecycle_history = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}/history", token=token_a,
        )
        terminal_events = [event.get("event_type") for event in lifecycle_history
                           if event.get("event_type") in {"closure_verified", "cancelled"}]
        require(status == 200 and len(terminal_events) == 1
                and any(event.get("event_type") == "reopened" for event in lifecycle_history),
                f"Conflicting lifecycle race produced invalid audit history: {status}: {lifecycle_history}")
        status, cross_person_loop_after_restart = request(
            "GET", f"/api/v1/open-loops/{open_loop_id}", token=token_b,
        )
        require(status == 404,
                f"Open Loop isolation failed after restart, got {status}: {cross_person_loop_after_restart}")

        status, hydration_after_restart = request("GET", "/api/situation", token=token_a)
        require(status == 200, f"hydration after application restart expected 200, got {status}: {hydration_after_restart}")
        observations_after_restart = [
            item.get("raw_text")
            for item in (hydration_after_restart.get("active_care_situation") or {}).get("observations", [])
        ]
        require(observation in observations_after_restart, "care evidence did not survive application restart")
        status, coverage_after_restart = request(
            "GET", f"/api/v1/change/coverage/{person_a}?{coverage_query}", token=token_a,
        )
        require(status == 200, f"coverage read after application restart expected 200, got {status}: {coverage_after_restart}")
        status, patterns_after_restart = request(
            "GET", f"/api/v1/change/patterns/{person_c}?subject_type=fall", token=token_c,
        )
        require(status == 200, f"pattern read after application restart expected 200, got {status}: {patterns_after_restart}")
        require(
            any(
                pattern.get("id") == persisted_recurrence_id
                and pattern.get("is_active") is True
                and set(pattern.get("event_ids") or []).issuperset(pattern_event_ids)
                for pattern in patterns_after_restart
            ),
            "source-linked recurrence pattern did not persist across Uvicorn restart",
        )
        require(
            any(row.get("id") == saved_coverage.get("id") for row in coverage_after_restart.get("records", [])),
            "observation coverage did not persist across application restart",
        )
        status, _ = request("GET", f"/api/v1/evidence/{person_a}", token=token_b)
        require(status == 403, f"record isolation failed after restart; expected 403, got {status}")

        print("PASS: PostgreSQL readiness, anonymous session creation, durable capture/hydration,")
        print("      cross-record denial, token rotation, and persistence after Uvicorn restart.")
    finally:
        if server.poll() is None:
            stop_server(server)


if __name__ == "__main__":
    main()
