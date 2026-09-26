// Bisect: find which direct import of situation-entry/pipeline.ts freezes the event loop.
const mods = [
  "@/lib/care-events/record-care-event",
  "@/lib/data-acquisition-resilience",
  "@/lib/care-situation-understanding",
  "@/lib/mvp-input-architecture",
  "@/lib/active-care-situation",
  "@/lib/thread-ingestion",
  "@/lib/living-care-record-ux/event-clarifiers",
  "@/lib/care-memory-layers",
  "@/lib/failure-resilience",
  "@/lib/trust-provenance",
  "@/lib/network-effect-moat",
  "@/lib/success-model",
  "@/lib/mvp-surface-area",
  "@/lib/continuous-execution-loop",
  "@/lib/behavior-interpretation-engine",
  "@/lib/continuity-decay-engine",
  "@/lib/north-star-experience",
  "@/lib/clarification-engine",
  "@/lib/memory-strategy-engine",
  "@/lib/trust-layer-engine",
  "@/lib/crisis-mode-interaction-layer",
  "@/lib/state-of-care-summary-engine",
  "@/lib/care-context-diff-engine",
  "@/lib/care-timeline-engine",
  "@/lib/task-extraction-engine",
  "@/lib/current-state-view-engine",
  "@/lib/adoption-wedge-engine",
  "@/lib/forbidden-build-zone",
  "@/lib/product-reality-model",
  "@/lib/timeline-reconstruction-engine",
  "@/lib/contradiction-detection-engine",
  "@/lib/care-state-change-detector",
  "@/lib/care-transparency-layer",
  "@/lib/input-relevance",
  "@/lib/final-output-contract/entry-compile",
  "@/lib/final-output-contract",
];

(async () => {
  console.log("start bisect");
  for (const m of mods) {
    process.stdout.write("IMPORTING " + m + "... ");
    try {
      const t0 = Date.now();
      const r = await Promise.race([
        import(m),
        new Promise<null>((_r, rej) => setTimeout(() => rej(new Error("TIMEOUT")), 8000)),
      ]);
      process.stdout.write("OK (" + (Date.now() - t0) + "ms)\n");
    } catch (e: any) {
      process.stdout.write("ERR: " + e?.message + "\n");
    }
  }
  console.log("bisect done");
})();
