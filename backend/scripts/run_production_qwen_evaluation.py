#!/usr/bin/env python3
"""Invoke the synthetic-only evaluation against the deployed SolenOS Qwen runtime.

Required environment:
  SOLENOS_PRODUCTION_API_BASE_URL  Base URL of the deployed backend.
  SOLENOS_QWEN_EVAL_TOKEN          Bearer token configured on that backend.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    base = os.getenv("SOLENOS_PRODUCTION_API_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("SOLENOS_QWEN_EVAL_TOKEN", "").strip()
    if not base or not token:
        print("Missing SOLENOS_PRODUCTION_API_BASE_URL or SOLENOS_QWEN_EVAL_TOKEN.", file=sys.stderr)
        return 2
    request = urllib.request.Request(
        base + "/api/v1/assistant/evaluate-production",
        data=b"{}",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"},
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

    print(json.dumps(payload, indent=2))
    expected = {
        "contradiction_preservation",
        "reporter_attribution",
        "event_chronology",
        "uncertainty",
        "medication_safety",
    }
    actual = {item.get("criterion") for item in payload.get("results", [])}
    if not payload.get("synthetic_only") or payload.get("care_record_accessed") is not False:
        print("Evaluation did not confirm synthetic-only isolation.", file=sys.stderr)
        return 1
    if actual != expected or payload.get("total") != 5 or payload.get("overall_passed") is not True:
        print("Production Qwen acceptance gate FAILED.", file=sys.stderr)
        return 1
    print("Production Qwen acceptance gate PASSED: all five criteria.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
