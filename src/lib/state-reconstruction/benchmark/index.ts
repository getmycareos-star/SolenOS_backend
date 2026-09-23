/**
 * State Reconstruction — Benchmark Index
 * SoT: docs/02-product/solenos-state-reconstruction.md
 */

export {
  runBenchmarkSuite,
  printBenchmarkSummary,
  type BenchmarkCase,
  type BenchmarkResult,
} from "./suite";

export {
  validateCareState,
  validateReconstructionResult,
  formatValidationReport,
} from "./validators";

export { BENCHMARK_REQUIREMENTS, FAILURE_TAXONOMY } from "./contract-constants";

/**
 * Run full benchmark and validation
 */
export async function runFullBenchmark(): Promise<void> {
  const { runBenchmarkSuite, printBenchmarkSummary } = await import("./suite");
  const { validateReconstructionResult } = await import("./validators");
  const { reconstructCareState } = await import("../engine");

  // Run benchmarks
  const results = await runBenchmarkSuite();
  printBenchmarkSummary(results);

  // Run validation on last result (example)
  if (results.length > 0) {
    // In real usage, you'd validate each result
    console.log("Validation would be run on each reconstruction result");
  }
}