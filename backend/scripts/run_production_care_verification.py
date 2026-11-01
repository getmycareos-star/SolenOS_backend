#!/usr/bin/env python3
"""Verify deployed Qwen and authenticated production care persistence with synthetic-only data.

This is intentionally a single-pass production probe: it does not retry slow
inference, change service timeouts, read real care records, or print bearer tokens.
The synthetic evidence remains in the production database as an explicitly
labelled audit fixture; its anonymous sessions are revoked in a finally block.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE = os.getenv(
    "SOLENOS_PRODUCTION_API_BASE_URL",
    "https://solenosbackend-production.up.railway.app",
).strip().rstrip("/")
RUN_ID = os.getenv("GITHUB_RUN_ID") or uuid.uuid4().hex


def call(method: str, path: str, *, payload=None, token=None, timeout=30):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read(8000).decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"response": raw}
        return exc.code, body


def require(status: int, expected: int, payload: dict, label: str):
    if status != expected:
        raise RuntimeError(
            f"{label} expected HTTP {expected}, received {status}: "
            + json.dumps(payload, ensure_ascii=False)[:3000]
        )
    return payload


def main() -> int:
    if not BASE:
        raise RuntimeError("Production API base URL is empty.")

    # Inventory and bounded inference readiness are separate checks.
    status, inventory = call("GET", "/api/v1/assistant/installation", timeout=15)
    inventory = require(status, 200, inventory, "Qwen installation inventory")
    installation = inventory.get("installation") or {}
    model = inventory.get("model")
    deployment_commit = inventory.get("deployment_commit")
    if (
        inventory.get("available") is not True
        or installation.get("state") != "installed"
        or not isinstance(model, str)
        or not model.strip()
        or not deployment_commit
    ):
        raise RuntimeError(
            "Configured Qwen model is not confirmed installed: "
            + json.dumps({
                "available": inventory.get("available"),
                "model": model,
                "deployment_commit": deployment_commit,
                "installation": installation,
            }, ensure_ascii=False)
        )

    readiness_status, readiness = call("GET", "/api/v1/assistant/readiness", timeout=20)
    readiness_ok = (
        readiness_status == 200
        and readiness.get("available") is True
        and readiness.get("model") == model
        and readiness.get("deployment_commit") == deployment_commit
    )
    readiness_failure = None if readiness_ok else {
        "http_status": readiness_status,
        "available": readiness.get("available"),
        "model": readiness.get("model"),
        "deployment_commit": readiness.get("deployment_commit"),
        "failure_reason": readiness.get("failure_reason"),
        "error_category": readiness.get("error_category"),
    }
    # Do not let a failed 12-second readiness probe suppress the real Companion
    # request. Record the probe result, then make one separately timed request
    # through the authenticated caregiver route without changing server timeouts.

    run_tag = f"synthetic-production-verification-{RUN_ID}-{uuid.uuid4().hex[:8]}"
    first_token = None
    active_token = None
    second_token = None
    try:
        # Create an isolated production care record through the public application API.
        status, created = call(
            "POST",
            "/api/v1/sessions/anonymous",
            payload={
                "name": run_tag,
                "caregiver_name": "SolenOS synthetic production verifier",
            },
            timeout=20,
        )
        created = require(status, 201, created, "Synthetic anonymous-session bootstrap")
        person_id = created["person"]["id"]
        caregiver_id = created["caregiver"]["id"]
        first_token = created["access_token"]
        active_token = first_token

        occurred_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        reported_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        collected_at = datetime.now(timezone.utc)
        source_text = (
            f"{run_tag}: synthetic QA fixture only. A test caregiver entered this "
            "record to verify persistence and retrieval. It describes no real person, "
            "care event, symptom, diagnosis, or clinical instruction."
        )
        evidence_payload = {
            "person_id": person_id,
            "type": "note",
            "evidence_kind": "caregiver_report",
            "epistemic_status": "unverified",
            "source_text": source_text,
            "uploaded_by_caregiver_id": caregiver_id,
            "reported_by_caregiver_id": caregiver_id,
            "reported_by_label": "Synthetic QA reporter",
            "collected_by_caregiver_id": caregiver_id,
            "original_author": "SolenOS synthetic production verifier",
            "occurred_at": occurred_at.isoformat(),
            "reported_at": reported_at.isoformat(),
            "collected_at": collected_at.isoformat(),
            "origin_channel": "synthetic_production_verification",
        }
        status, evidence = call(
            "POST",
            "/api/v1/evidence",
            payload=evidence_payload,
            token=active_token,
            timeout=30,
        )
        evidence = require(status, 200, evidence, "Synthetic evidence write")
        evidence_id = evidence.get("id")
        if not evidence_id or evidence.get("source_text") != source_text or not evidence.get("source_text_sha256"):
            raise RuntimeError("Evidence write response did not preserve the original synthetic source text and digest.")

        # A separate HTTP request retrieves the record from the application API.
        status, rows = call(
            "GET",
            f"/api/v1/evidence/{person_id}",
            token=active_token,
            timeout=20,
        )
        rows = require(status, 200, rows, "Initial synthetic evidence retrieval")
        if not any(row.get("id") == evidence_id and row.get("source_text") == source_text for row in rows):
            raise RuntimeError("Synthetic evidence was not retrievable after the write.")

        # Rotate the server-issued capability, then prove fresh-session retrieval.
        status, refreshed = call(
            "POST",
            "/api/v1/sessions/refresh",
            token=active_token,
            timeout=20,
        )
        refreshed = require(status, 200, refreshed, "Anonymous session refresh")
        active_token = refreshed["access_token"]
        if refreshed.get("person_id") != person_id or refreshed.get("caregiver_id") != caregiver_id:
            raise RuntimeError("Session refresh changed care-record identity.")

        status, old_token_result = call(
            "GET",
            f"/api/v1/evidence/{person_id}",
            token=first_token,
            timeout=15,
        )
        if status != 401:
            raise RuntimeError(f"Rotated bearer token was not invalidated; expected 401, got {status}.")

        status, fresh_rows = call(
            "GET",
            f"/api/v1/evidence/{person_id}",
            token=active_token,
            timeout=20,
        )
        fresh_rows = require(status, 200, fresh_rows, "Fresh-session synthetic evidence retrieval")
        persisted = next((row for row in fresh_rows if row.get("id") == evidence_id), None)
        if not persisted or persisted.get("source_text") != source_text:
            raise RuntimeError("Fresh session could not retrieve the exact persisted synthetic source.")
        if persisted.get("uploaded_by_caregiver_id") != caregiver_id:
            raise RuntimeError("Fresh-session evidence uploader attribution changed.")

        # A second server-issued identity must not read the first person's evidence.
        status, other = call(
            "POST",
            "/api/v1/sessions/anonymous",
            payload={
                "name": run_tag + "-isolation-check",
                "caregiver_name": "SolenOS synthetic isolation verifier",
            },
            timeout=20,
        )
        other = require(status, 201, other, "Second synthetic anonymous-session bootstrap")
        second_token = other["access_token"]
        status, denied = call(
            "GET",
            f"/api/v1/evidence/{person_id}",
            token=second_token,
            timeout=15,
        )
        if status != 403:
            raise RuntimeError(f"Cross-person evidence read expected HTTP 403, received {status}.")

        # One real authenticated Companion request. No readiness preflight retry,
        # synthetic response, or increased server timeout is substituted here.
        companion_text = (
            "Please summarize the synthetic verification note in one sentence. "
            "State explicitly that it is synthetic test data and do not infer real care facts."
        )
        started = time.perf_counter()
        status, companion = call(
            "POST",
            "/api/v1/conversation/message",
            payload={"person_id": person_id, "text": companion_text},
            token=active_token,
            timeout=60,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        if status != 200:
            print(json.dumps({
                "production_deployment_commit": deployment_commit,
                "qwen_model": model,
                "qwen_installation_state": installation.get("state"),
                "qwen_readiness": "PASS" if readiness_ok else "FAIL",
                "qwen_readiness_failure": readiness_failure,
                "authenticated_companion_http_status": status,
                "companion_inference_duration_ms": elapsed_ms,
                "companion_error_response": companion,
            }, indent=2, ensure_ascii=False), file=sys.stderr)
            raise RuntimeError(
                "Authenticated Companion inference failed: "
                + json.dumps(companion, ensure_ascii=False)[:3000]
            )
        companion = require(status, 200, companion, "Authenticated Companion inference")
        reply = companion.get("reply")
        if (
            companion.get("ai_generated") is not True
            or not isinstance(reply, str)
            or not reply.strip()
            or companion.get("model") != model
            or companion.get("written_to_lcr") is not False
            or evidence_id not in companion.get("source_evidence_ids", [])
        ):
            raise RuntimeError(
                "Companion response did not prove non-empty inference on the installed model "
                "with the synthetic source and read-only LCR boundary: "
                + json.dumps({
                    "ai_generated": companion.get("ai_generated"),
                    "model": companion.get("model"),
                    "written_to_lcr": companion.get("written_to_lcr"),
                    "source_evidence_ids_include_fixture": evidence_id in companion.get("source_evidence_ids", []),
                    "reply_present": bool(reply and reply.strip()),
                }, ensure_ascii=False)
            )

        print(json.dumps({
            "production_deployment_commit": deployment_commit,
            "qwen_model": model,
            "qwen_installation_state": installation.get("state"),
            "qwen_readiness": "PASS" if readiness_ok else "FAIL",
            "qwen_readiness_failure": readiness_failure,
            "synthetic_evidence_write": "PASS",
            "fresh_session_retrieval": "PASS",
            "old_token_revocation": "PASS",
            "cross_person_isolation": "PASS",
            "authenticated_companion_inference": "PASS",
            "companion_inference_duration_ms": elapsed_ms,
            "companion_reply": reply[:500],
            "companion_used_synthetic_evidence": True,
            "companion_wrote_to_lcr": companion.get("written_to_lcr"),
            "synthetic_person_id": person_id,
            "synthetic_evidence_id": evidence_id,
            "synthetic_record_retained_for_audit": True,
        }, indent=2, ensure_ascii=False))
        return 0 if readiness_ok else 1
    finally:
        # Revocation preserves the labelled synthetic audit fixture and its evidence.
        for token in (active_token, second_token):
            if token:
                try:
                    call("POST", "/api/v1/sessions/revoke", token=token, timeout=10)
                except (URLError, TimeoutError, ValueError):
                    pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PRODUCTION_CARE_VERIFICATION_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
