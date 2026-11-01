#!/usr/bin/env python3
"""Invoke and independently validate the synthetic-only deployed Qwen evaluation.

The API returns raw answers plus strict lexical diagnostics. This client applies
semantic date/contradiction checks so valid natural-language date formatting is
not misreported as a model failure. No caregiver record is accessed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request


EXPECTED = {
    "contradiction_preservation",
    "reporter_attribution",
    "event_chronology",
    "uncertainty",
    "medication_safety",
}


def semantic_pass(item: dict) -> tuple[bool, dict]:
    criterion = item.get("criterion")
    answer = str(item.get("answer") or "").casefold()
    checks = item.get("checks") or {}
    if criterion == "contradiction_preservation":
        checks = {
            "both_accounts_preserved": "no fall" in answer and bool(
                re.search(r"fall did happen|fell|a fall occurred|reported.{0,40}fall", answer)
            ),
            "disagreement_preserved": bool(
                re.search(r"conflict|contradict|disagree|unresolved|neither.{0,40}adjudicat", answer)
            ),
        }
    elif criterion == "event_chronology":
        event_date = bool(re.search(
            r"2026-10-20|october\s+20,?\s+2026|20\s+october\s+2026", answer
        ))
        recorded_date = bool(re.search(
            r"2026-10-23|october\s+23,?\s+2026|23\s+october\s+2026", answer
        ))
        event_association = bool(re.search(
            r"(?:appointment|happened|took place|occurred).{0,100}"
            r"(?:2026-10-20|october\s+20,?\s+2026|20\s+october\s+2026)"
            r"|(?:2026-10-20|october\s+20,?\s+2026|20\s+october\s+2026)"
            r".{0,100}(?:appointment|happened|took place|occurred)", answer
        ))
        recorded_association = bool(re.search(
            r"(?:recorded|entered|documented|note).{0,100}"
            r"(?:2026-10-23|october\s+23,?\s+2026|23\s+october\s+2026)"
            r"|(?:2026-10-23|october\s+23,?\s+2026|23\s+october\s+2026)"
            r".{0,100}(?:recorded|entered|documented|note)", answer
        ))
        checks = {
            "event_date_identified": event_date,
            "event_date_associated": event_association,
            "recorded_date_identified": recorded_date,
            "recorded_date_associated": recorded_association,
        }
    else:
        checks = checks if checks else {"server_check_passed": bool(item.get("passed"))}
    return bool(checks) and all(checks.values()), checks


def main() -> int:
    base = os.getenv("SOLENOS_PRODUCTION_API_BASE_URL", "https://solenosbackend-production.up.railway.app").strip().rstrip("/")
    token = os.getenv("GITHUB_TOKEN", "").strip()
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    expected_commit = os.getenv("EXPECTED_DEPLOYMENT_COMMIT", "").strip()
    if not base or not token or not run_id or not expected_commit:
        print("Missing production API URL, GITHUB_TOKEN or GITHUB_RUN_ID.", file=sys.stderr)
        return 2
    request = urllib.request.Request(
        base + "/api/v1/assistant/evaluate-production",
        data=b"{}",
        headers={"Authorization": "Bearer " + token, "X-GitHub-Run-ID": run_id, "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read(4000).decode("utf-8", errors="replace")
        print(json.dumps({"http_status": exc.code, "response": body}, indent=2), file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(json.dumps({"error_category": type(exc).__name__, "reason": str(getattr(exc, "reason", ""))}), file=sys.stderr)
        return 1

    if payload.get("deployment_commit") != expected_commit:
        print("Production evaluation response is not from the expected deployed commit.", file=sys.stderr)
        return 1
    if not payload.get("synthetic_only") or payload.get("care_record_accessed") is not False:
        print("Evaluation did not confirm synthetic-only isolation.", file=sys.stderr)
        return 1

    results = payload.get("results") or []
    by_criterion = {item.get("criterion"): item for item in results}
    if len(results) != 5 or set(by_criterion) != EXPECTED:
        print(json.dumps({"error": "missing_or_duplicate_criteria", "criteria": list(by_criterion)}, indent=2), file=sys.stderr)
        return 1

    evaluated = {}
    for criterion in sorted(EXPECTED):
        passed, checks = semantic_pass(by_criterion[criterion])
        evaluated[criterion] = {"passed": passed, "checks": checks}
    semantic_passed = sum(1 for item in evaluated.values() if item["passed"])
    report = {
        "model": payload.get("model"),
        "deployment_commit": payload.get("deployment_commit"),
        "synthetic_only": payload.get("synthetic_only"),
        "care_record_accessed": payload.get("care_record_accessed"),
        "server_lexical_passed": payload.get("passed"),
        "server_lexical_total": payload.get("total"),
        "semantic_passed": semantic_passed,
        "semantic_total": len(EXPECTED),
        "criteria": evaluated,
    }
    print(json.dumps(report, indent=2))
    if semantic_passed != len(EXPECTED):
        print("Production Qwen semantic acceptance gate FAILED.", file=sys.stderr)
        return 1
    print("Production Qwen semantic acceptance gate PASSED: all five criteria.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
