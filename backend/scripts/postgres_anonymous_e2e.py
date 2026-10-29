"""Real HTTP + PostgreSQL smoke test for anonymous care continuity.

Runs only against the disposable PostgreSQL database supplied by CI. Starts and
restarts Uvicorn itself so persistence is verified across an application restart.
"""
from __future__ import annotations

import json
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

        stop_server(server)
        server = start_server()
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
