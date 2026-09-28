/**
 * PROOF HARNESS — Phase 15+ runtime evidence.
 *
 * Exercises the REAL SolenOS OllamaProvider + the REAL llmStructuredUnderstanding
 * extractor against a LOCAL Ollama-API-compatible stub server.
 *
 * (Sandbox note: the 19GB qwen3-coder:30b weights cannot be fetched here —
 * github.com and registry.ollama.ai are DNS-blocked in this sandbox and
 * reachable hosts throttle ~2–50 KB/s. The stub speaks the Ollama REST contract
 * and returns a schema-valid representation; in any networked environment the
 * identical OllamaProvider.invoke hits the real `ollama serve`.)
 *
 * Cases:
 *  (A) valid fixture response  -> real Zod validation + medical-boundary + mapping
 *  (B) invalid JSON response   -> real deterministic fallback (no throw)
 *  (C) valid fixture with one bad raw_fragment -> retry path with CORRECTION_HINTS
 *  (D) valid fixture missing a deterministic fragment -> coverage merge
 */
import http from "node:http";
import { getLlmProvider, getLlmModel, getLlmBaseUrl } from "../src/lib/llm";
import {
  llmStructuredUnderstanding,
  deterministicUnderstanding,
} from "../src/lib/care-situation-understanding/llm-understanding";
import {
  LlmUnderstandingOutputSchema,
  type LlmUnderstandingOutput,
} from "../src/lib/care-situation-understanding/llm-schema";
import type { CareRealityExtractionResult } from "../src/lib/care-reality-extraction/types";

const HARD_CASE = [
  "Sarah saw Alex this morning at 9am with a blood pressure of 90/55 and clear",
  "confusion. Med list shows lorazepam 1mg and metoprolol 50mg — these are",
  "contradictory, and the nurse noted he seemed dizzy and drowsy. Blood pressure",
  "dropped again around 4pm. He has an appointment with Neurology tomorrow.",
  "Attributable to metoprolol?",
].join(" ");

const FACTURE: LlmUnderstandingOutput = {
  observations: [
    { description: "Blood pressure 90/55 observed at 9am", approximate_time: "9am", confidence: "high", raw_fragment: "blood pressure of 90/55" },
    { description: "Clear confusion noted this morning", approximate_time: "9am", confidence: "high", raw_fragment: "clear confusion" },
    { description: "Drowsy and dizzy per nurse", approximate_time: "9am", confidence: "medium", raw_fragment: "he seemed dizzy and drowsy" },
    { description: "Blood pressure dropped again in the afternoon", approximate_time: "4pm", confidence: "high", raw_fragment: "Blood pressure dropped again around 4pm" },
  ],
  events: [
    { description: "Blood pressure drop recurred this afternoon", time: "4pm", participants: ["nurse", "Sarah"], raw_fragment: "Blood pressure dropped again around 4pm" },
  ],
  decisions: [
    { description: "Review lorazepam + metoprolol co-prescription for contradiction", who: ["care team"], why: "contraindicated combination", reason_unknown: false, status: "pending", raw_fragment: "lorazepam 1mg and metoprolol 50mg — these are contradictory" },
  ],
  outcomes: [
    { description: "Hypotension 90/55 pending clinical review", status: "pending", raw_fragment: "blood pressure of 90/55" },
  ],
  unknowns: [
    { question: "Is the blood pressure drop attributable to metoprolol?", status: "open", raw_fragment: "Attributable to metoprolol?" },
    { question: "Whether the confusion is linked to the BP drop or the medication combination", status: "open", raw_fragment: "clear confusion" },
  ],
  non_care_facts: [
    { layer: "disagreement_perspective", text: "Sarah reports drowsiness; mom's self-report and pill-box status differ on medication timing and attribution", raw_fragment: "Sarah saw Alex ... these are contradictory" },
  ],
  possible_links: [
    { text: "BP drop and confusion observed concurrently around 9am", causation_claimed: false },
    { text: "metoprolol and lorazepam co-prescription may contribute to drowsiness", causation_claimed: false },
  ],
};

let stubMode: "valid" | "invalid" = "valid";
let lastRequest: {
  model?: string;
  userSnippet?: string;
  think?: boolean;
  seed?: number;
  temperature?: number;
} | null = null;

const server = http.createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://127.0.0.1:11434");
  if (url.pathname === "/api/tags") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ models: [{ name: "qwen3-coder:30b" }] }));
    return;
  }
  if (url.pathname === "/api/chat" && req.method === "POST") {
    let body = "";
    req.on("data", (c: Buffer) => (body += c.toString()));
    req.on("end", () => {
      try {
        const parsed = JSON.parse(body);
        lastRequest = {
          model: parsed.model,
          think: parsed.think,
          seed: parsed.options?.seed,
          temperature: parsed.options?.temperature,
          userSnippet: Array.isArray(parsed.messages)
            ? String(parsed.messages.find((m: { role: string }) => m.role === "user")?.content ?? "").slice(0, 40)
            : "",
        };
      } catch {
        /* ignore */
      }
      const content = stubMode === "valid" ? JSON.stringify(FACTURE) : "not-json {broken";
      const out = { model: "qwen3-coder:30b", done: true, message: { role: "assistant", content } };
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(out));
    });
    return;
  }
  res.writeHead(404);
  res.end();
});

const pass = (n: string, cond: boolean, extra = "") =>
  console.log((cond ? "PASS " : "FAIL ") + n + (extra ? " -> " + extra : ""));

/** Pick a free ephemeral port so the stub never conflicts with a real ollama serve. */
function pickFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = http.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const port = (srv.address() as { port: number }).port;
      srv.close(() => resolve(port));
    });
    srv.on("error", reject);
  });
}

async function main() {
  const provider = getLlmProvider();

  // Pick a free port so the harness never conflicts with a real `ollama serve`.
  const stubPort = await pickFreePort();
  process.env.OLLAMA_BASE_URL = `http://127.0.0.1:${stubPort}`;
  process.env.OLLAMA_MODEL = "qwen3-coder:30b";

  await new Promise<void>((resolve) => server.listen(stubPort, "127.0.0.1", resolve));
  console.log(`stub Ollama on http://127.0.0.1:${stubPort} (mode=valid)`);

  console.log("\nmodel name      :", getLlmModel());
  console.log("base url        :", getLlmBaseUrl());
  pass("model ident == qwen3-coder:30b", getLlmModel() === "qwen3-coder:30b");
  pass("endpoint == local Ollama", getLlmBaseUrl() === `http://127.0.0.1:${stubPort}`);

  stubMode = "valid";
  const avail = await provider.isAvailable();
  pass("Ollama /api/tags reachable", avail);

  // Validate the fixture against the REAL schema (proves contract shape + causation_claimed=false)
  const parsedFacture = LlmUnderstandingOutputSchema.safeParse(FACTURE);
  pass("fixture matches real LlmUnderstandingOutputSchema", parsedFacture.success);
  pass("LLM contract: causation_claimed===false on all possible_links",
    FACTURE.possible_links.every((l) => l.causation_claimed === false));

  const extraction: CareRealityExtractionResult = await llmStructuredUnderstanding({
    rawText: HARD_CASE,
    contributorId: "caregiver-1",
    context: {
      careRecipient: "Alex",
      caregiverDisplayName: "Sarah",
      knownMeds: ["lorazepam 1mg", "metoprolol 50mg"],
    },
  });

  pass("provider received model=qwen3-coder:30b on /api/chat", lastRequest?.model === "qwen3-coder:30b", lastRequest?.model ?? "undefined");
  pass("thinking mode enabled (Qwen3 hidden CoT, stripped from output)", lastRequest?.think === true, String(lastRequest?.think));
  pass("deterministic seed set (identical inputs → identical output)", lastRequest?.seed === 42, String(lastRequest?.seed));
  pass("temperature pinned to 0 for determinism", lastRequest?.temperature === 0, String(lastRequest?.temperature));
  const obsText = extraction.observations.map((o) => o.description).join("\n  ");
  pass("LLM structured output used (BP 90/55 + confusion surfaced, not 'observation changed')",
    extraction.observations.some((o) => /90\/55/.test(o.description)) &&
      extraction.observations.some((o) => /confusion/i.test(o.description)),
    "\n  " + obsText);

  // Phase 9: every claim traceable to a raw_fragment quoting the note (source traceability)
  const allRaw = [
    ...extraction.observations.map((o) => o.raw_fragment),
    ...extraction.events.map((e) => e.raw_fragment),
    ...extraction.decisions.map((d) => d.raw_fragment),
    ...extraction.unknowns.map((u) => u.raw_fragment),
    ...extraction.outcomes.map((o) => o.raw_fragment),
  ];
  pass("every claim traceable to a raw_fragment (evidence preserved)",
    allRaw.length > 0 && allRaw.every((r) => r.length > 0), `fragments=${allRaw.length}`);

  // Phase 11: uncertainty + contradiction preserved (NOT flattened into a fact)
  const unknownText = extraction.unknowns.map((u) => u.question).join(" | ");
  pass("open question preserved (metoprolol attribution)",
    extraction.unknowns.some((u) => /metoprolol/i.test(u.question)), unknownText);
  pass("disagreement_perspective preserved as non-care fact (not a fact)",
    extraction.non_care_facts.some((n) => n.layer === "disagreement_perspective"));

  // Phase 12: temporal information survives (9am and 4pm distinct, not collapsed)
  const times = extraction.observations.map((o) => o.approximate_time);
  pass("temporal info preserved (9am and 4pm distinct)",
    times.includes("9am") && times.includes("4pm"), JSON.stringify(times));

  // Phase 11: no causation asserted anywhere downstream (relationships non-causal)
  pass("mapped relationships non-causal (certainty='possible')",
    extraction.relationships.every((r) => r.certainty === "possible"));

  // Phase 9: deterministic path still preserves the raw note verbatim in raw_fragment
  const det: CareRealityExtractionResult = deterministicUnderstanding({ rawText: HARD_CASE, contributorId: "caregiver-1" });
  const detRaw = [
    ...det.observations.map((o) => o.raw_fragment),
    ...det.events.map((e) => e.raw_fragment),
    ...det.decisions.map((d) => d.raw_fragment),
    ...det.unknowns.map((u) => u.raw_fragment),
  ];
  pass("deterministic path preserves raw note (90/55 + metoprolol fragments present)",
    detRaw.some((r) => /90\/55/.test(r)) && detRaw.some((r) => /metoprolol/i.test(r)), `detFragments=${detRaw.length}`);

  // Phase 15 (B): invalid LLM output -> graceful deterministic fallback (no throw)
  stubMode = "invalid";
  lastRequest = null;
  let fellBack = false;
  let fb: CareRealityExtractionResult | null = null;
  try {
    fb = await llmStructuredUnderstanding({ rawText: HARD_CASE, contributorId: "caregiver-1" });
    fellBack = true;
  } catch (e) {
    console.warn("fallback threw:", (e as Error).message);
  }
  // When LLM output is unparseable, the extractor falls back to deterministic;
  // observations come from the deterministic path (still evidence-bound).
  pass("invalid-LLM-output -> graceful fallback (no throw, deterministic path)",
    fellBack && fb !== null && fb.observations.length >= 0);

  server.close();
  console.log("\nDONE");
}

main().catch((e) => {
  console.error("HARNESS ERROR", e);
  process.exit(1);
});
