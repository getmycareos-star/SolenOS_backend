"""Manual smoke check for the local reasoning layer.

Not part of pytest. Run directly when the trust guard or the prompts change and
you want to see what the real local model actually does, rather than what a mock
was told it does.

    python -m scripts.llm_smoke
"""

from __future__ import annotations

import json
import sys

from app.core.llm_enums import LLMTask
from app.services.llm import LocalReasoningEngine
from app.services.llm.client import OllamaClient


EVIDENCE = {
    "ev-real-1": "Mum was up four times in the night again. She was looking for her mother when she came out.",
    "ev-real-2": "She ate half her lunch today and said the food was too strong.",
}


def show(title: str, payload: dict) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def main() -> int:
    engine = LocalReasoningEngine()
    print("health:", json.dumps(engine.health(), default=str))

    if not engine.is_available():
        print("local model unavailable; nothing to smoke-test")
        return 1

    payload = {
        "person_profile": {
            "evidence": EVIDENCE,
            "recent_caregiver_notes": [
                "Mum used to be a schoolteacher. She still corrects the news.",
                "She hates the radio on in the morning.",
            ],
        }
    }

    for task, data in payload.items():
        parsed, raw, meta = _run(engine, task, data)
        show(f"{task} raw", {"raw": raw[:1500], "meta": meta})
        show(f"{task} parsed", parsed)
    return 0


def _run(engine, task, data):
    client: OllamaClient = engine.client
    from app.services.llm.prompts import get_prompt

    ok, parsed, raw, meta = client.generate_json(get_prompt(task), data)
    if ok:
        from app.services.llm.trust_guard import TrustGuard

        guard = TrustGuard(
            permitted_evidence_ids=list(EVIDENCE),
            evidence_texts=EVIDENCE,
        ).guard_insight(parsed)
        show(f"{task} guard", {
            "verdict": guard.verdict,
            "reasons": guard.reasons,
            "qualifications": guard.qualifications,
            "dropped_evidence_ids": guard.dropped_evidence_ids,
            "output_evidence_ids": guard.output_evidence_ids,
            "confidence": guard.confidence,
            "claims": [c.value for c in guard.claims],
        })
        show(f"{task} guarded payload", guard.payload)
    return parsed, raw, meta


if __name__ == "__main__":
    sys.exit(main())
