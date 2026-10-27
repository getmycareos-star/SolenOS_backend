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
        status, revoked = request("/api/v1/sessions/revoke", body={}, token=recovery_token)
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

        status, refreshed = request("POST", "/api/v1/sessions/refresh", token=token_a)
        require(status == 200, f"token refresh expected 200, got {status}: {refreshed}")
        token_a = refreshed["access_token"]
        status, _ = request("GET", f"/api/v1/evidence/{person_a}", token=first["access_token"])
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
        status, _ = request("GET", f"/api/v1/evidence/{person_a}", token=token_b)
        require(status == 403, f"record isolation failed after restart; expected 403, got {status}")

        print("PASS: PostgreSQL readiness, anonymous session creation, durable capture/hydration,")
        print("      cross-record denial, token rotation, and persistence after Uvicorn restart.")
    finally:
        if server.poll() is None:
            stop_server(server)


if __name__ == "__main__":
    main()
