"""Live Qwen Companion behavior evaluation; run only against real inference in CI.

The evaluator checks behavioral properties, not exact canned wording. It uses an
empty CI care record so conversational statements must not be upgraded into LCR facts.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request


def post_json(url: str, token: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise AssertionError(f"Companion returned HTTP {exc.code}: {detail}") from exc
    assert isinstance(data, dict), "Companion response must be a JSON object"
    assert data.get("ai_generated") is True, "Expected real model-generated Companion response"
    assert data.get("guard_verdict") == "passed", "Response did not pass the server guard"
    assert data.get("written_to_lcr") is False, "Conversation must not write to the LCR"
    assert data.get("persistence") == "conversation_only", "Conversation persistence boundary changed"
    assert isinstance(data.get("reply"), str) and data["reply"].strip(), "Empty Companion reply"
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--person-id", required=True)
    args = parser.parse_args()
    endpoint = args.base_url.rstrip("/") + "/api/v1/conversation/message"

    first = post_json(endpoint, args.token, {
        "person_id": args.person_id,
        "text": "Mum's sleep has been bad this week.",
    })
    session_id = first.get("session_id")
    assert session_id, "Companion did not return a conversation session ID"

    second = post_json(endpoint, args.token, {
        "person_id": args.person_id,
        "session_id": session_id,
        "text": "Does the care record show that her sleep is getting worse?",
    })
    reply = second["reply"].casefold()
    uncertainty = re.search(
        r"\b(?:cannot|can't|can not|don't|do not|doesn't|does not|unable|"
        r"not recorded|not enough|insufficient|no (?:earlier |previous |matching )?"
        r"(?:record|history|information|evidence)|unknown|can't tell|cannot tell)\b",
        reply,
    )
    record_scope = re.search(r"\b(?:record|recorded|history|evidence|information|trend)\b", reply)
    assert uncertainty and record_scope, (
        "Grounding evaluation failed: Qwen must state that the empty care record "
        "does not establish a sleep trend. Reply was: " + second["reply"]
    )

    third = post_json(endpoint, args.token, {
        "person_id": args.person_id,
        "session_id": session_id,
        "text": "What did I tell you about her sleep?",
    })
    continuity = third["reply"].casefold()
    assert re.search(r"\bsleep\b", continuity), (
        "Conversation continuity evaluation failed: reply did not resolve the sleep reference. "
        "Reply was: " + third["reply"]
    )
    attribution = re.search(
        r"\b(?:you said|you mentioned|you told me|you reported|you shared|you described|"
        r"you said that|you mentioned that|you told me that|you reported that|"
        r"you shared that|you described that|you've said|you've mentioned)\b",
        continuity,
    )
    assert attribution, (
        "Provenance evaluation failed: Qwen must attribute the sleep statement to the "
        "caregiver's prior message, not present it as a recorded care fact. Reply was: "
        + third["reply"]
    )

    print(json.dumps({
        "evaluation": "companion_grounding_and_continuity",
        "model_inference": "real",
        "cases_passed": 3,
        "checks": [
            "real generated response and server guard contract",
            "conversation-only persistence; no LCR write",
            "care-record question does not convert conversation-only report into a recorded trend",
            "follow-up reference resolves to prior conversation",
            "prior caregiver statement remains explicitly attributed",
        ],
        "replies": [first["reply"], second["reply"], third["reply"]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"COMPANION LIVE EVALUATION FAILED: {exc}", file=sys.stderr)
        raise
