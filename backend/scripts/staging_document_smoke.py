"""Live staging smoke test for scoped document intake and OCR runtime.

Requires DATABASE_URL to point at the same disposable staging PostgreSQL database
used by STAGING_API_BASE_URL. The script creates uniquely named synthetic records
and deletes only those records in a finally block. It never prints bearer tokens
or uploaded document content.
"""
from __future__ import annotations

import os
import sys
import time
from urllib.parse import urlparse

import httpx
from sqlalchemy import create_engine, text


def fail(message: str) -> None:
    raise RuntimeError(message)


def main() -> None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    api_origin = os.getenv("STAGING_API_BASE_URL", "").strip().rstrip("/")
    if not database_url or not database_url.startswith(("postgresql://", "postgresql+psycopg2://")):
        fail("DATABASE_URL must be a PostgreSQL staging connection string.")
    if not api_origin.startswith("https://"):
        fail("STAGING_API_BASE_URL must be the HTTPS origin of the deployed staging API.")

    api_base = api_origin[:-7] if api_origin.endswith("/api/v1") else api_origin
    api = api_base + "/api/v1"
    engine = create_engine(database_url, pool_pre_ping=True)
    created: list[dict] = []
    stamp = f"{int(time.time())}-{os.getpid()}"
    payload = (
        "Discharge summary.\n"
        "Follow-up appointment within two weeks.\n"
        "Medication dose 10 mg.\n"
    ).encode("utf-8")
    try:
        with engine.connect() as connection:
            if connection.dialect.name != "postgresql":
                fail("The configured staging database is not PostgreSQL.")
            current = connection.execute(text("SELECT current_database()")).scalar_one()
            print(f"Staging database connection verified (database name: {current}).")

        with httpx.Client(timeout=45.0, follow_redirects=False) as client:
            ready = client.get(api_base + "/ready")
            if ready.status_code != 200 or ready.json().get("status") != "ready":
                fail(f"Staging API readiness failed with HTTP {ready.status_code}.")

            def new_session(label: str) -> dict:
                response = client.post(
                    api + "/sessions/anonymous",
                    json={"name": f"STAGING-SMOKE-{stamp}-{label}", "caregiver_name": f"QA-{label}"},
                )
                if response.status_code != 201:
                    fail(f"Could not create a disposable scoped test session (HTTP {response.status_code}).")
                data = response.json()
                if not data.get("access_token") or not data.get("person", {}).get("id") or not data.get("caregiver", {}).get("id"):
                    fail("Staging session bootstrap did not return a complete scoped capability.")
                created.append({"person_id": data["person"]["id"], "caregiver_id": data["caregiver"]["id"]})
                return data

            owner = new_session("owner")
            other = new_session("other")
            owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
            other_headers = {"Authorization": f"Bearer {other['access_token']}"}

            ocr = client.get(api + "/documents/ocr-status", headers=owner_headers)
            if ocr.status_code != 200:
                fail(f"OCR runtime status endpoint failed with HTTP {ocr.status_code}.")
            ocr_data = ocr.json()
            if not ocr_data.get("binary_found") or not ocr_data.get("available") or "eng" not in ocr_data.get("languages", []):
                fail("The deployed host does not have a working Tesseract binary with English language data.")
            print(f"Hosting OCR verified (Tesseract {ocr_data.get('version')}; English language data available).")

            uploaded = client.post(
                api + "/documents/intake",
                headers=owner_headers,
                files={"file": (f"staging-smoke-{stamp}.txt", payload, "text/plain")},
            )
            if uploaded.status_code != 201:
                fail(f"Authenticated document intake failed with HTTP {uploaded.status_code}.")
            doc = uploaded.json()
            evidence_id = doc.get("evidence_id")
            if not evidence_id or not doc.get("source_file_preserved") or doc.get("sha256") is None:
                fail("Document intake did not confirm source-file preservation and digest.")
            if doc.get("candidate_count", 0) < 1:
                fail("Document intake did not create any source-quoted review candidates.")
            print("Authenticated upload and source-linked candidate extraction passed.")

            downloaded = client.get(api + f"/documents/{evidence_id}/file", headers=owner_headers)
            if downloaded.status_code != 200 or downloaded.content != payload:
                fail("Original-file download did not return the exact uploaded bytes.")
            if "no-store" not in downloaded.headers.get("cache-control", "").lower():
                fail("Original-file download is missing private no-store caching headers.")
            print("Original-file download integrity and private caching passed.")

            cross_record = client.get(api + f"/documents/{evidence_id}/file", headers=other_headers)
            if cross_record.status_code not in {403, 404}:
                fail(f"Cross-care-record file access was not denied (HTTP {cross_record.status_code}).")
            print("Cross-care-record download denial passed.")

            review = client.post(
                api + f"/documents/{evidence_id}/candidates/0/review",
                headers=owner_headers,
                json={"decision": "reject"},
            )
            if review.status_code != 200:
                fail(f"Candidate review failed with HTTP {review.status_code}.")
            review_data = review.json()
            candidate = review_data.get("candidate") or {}
            if candidate.get("reviewed_by_caregiver_id") != owner["caregiver"]["id"]:
                fail("Candidate review was not attributed to the authenticated caregiver.")
            if not candidate.get("reviewed_at") or review_data.get("authoritative_lcr_write") is not False:
                fail("Candidate review attribution or review-only semantics are missing.")
            repeat = client.post(
                api + f"/documents/{evidence_id}/candidates/0/review",
                headers=owner_headers,
                json={"decision": "reject"},
            )
            if repeat.status_code != 409:
                fail("A second decision was not rejected as an already-reviewed candidate.")
            print("Persisted caregiver review attribution and duplicate-review rejection passed.")

    finally:
        # Delete only the synthetic records created by this run. The evidence and
        # original bytes are tied to these fresh person IDs, never existing records.
        try:
            with engine.begin() as connection:
                for row in created:
                    person_id = row["person_id"]
                    caregiver_id = row["caregiver_id"]
                    for table in ("document_assets", "capture_analyses", "care_events", "evidence", "anonymous_sessions"):
                        if table == "document_assets":
                            connection.execute(text("DELETE FROM document_assets WHERE person_id = :pid"), {"pid": person_id})
                        elif table == "capture_analyses":
                            connection.execute(text("DELETE FROM capture_analyses WHERE person_id = :pid"), {"pid": person_id})
                        elif table == "care_events":
                            connection.execute(text("DELETE FROM care_events WHERE person_id = :pid"), {"pid": person_id})
                        elif table == "evidence":
                            connection.execute(text("DELETE FROM evidence WHERE person_id = :pid"), {"pid": person_id})
                        elif table == "anonymous_sessions":
                            connection.execute(text("DELETE FROM anonymous_sessions WHERE person_id = :pid"), {"pid": person_id})
                    connection.execute(text("DELETE FROM caregivers WHERE id = :cid AND person_id = :pid"), {"cid": caregiver_id, "pid": person_id})
                    connection.execute(text("DELETE FROM persons WHERE id = :pid"), {"pid": person_id})
        except Exception as exc:
            print(f"WARNING: synthetic staging cleanup failed; inspect only STAGING-SMOKE-{stamp} records. ({type(exc).__name__})", file=sys.stderr)
        engine.dispose()


if __name__ == "__main__":
    main()
